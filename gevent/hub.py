# Copyright (c) 2009-2010 Denis Bilenko. See LICENSE for details.

import sys
import os
import traceback
from gevent import core


__all__ = ['getcurrent',
           'GreenletExit',
           'spawn_raw',
           'sleep',
           'kill',
           'signal',
           'fork',
           'shutdown',
           'get_hub',
           'Hub',
           'Waiter']


def __import_py_magic_greenlet():
    try:
        from py.magic import greenlet
        return greenlet
    except ImportError:
        pass

try:
    greenlet = __import__('greenlet').greenlet
except ImportError:
    greenlet = __import_py_magic_greenlet()
    if greenlet is None:
        raise

getcurrent = greenlet.getcurrent
GreenletExit = greenlet.GreenletExit
MAIN = greenlet.getcurrent()

thread = __import__('thread')
threadlocal = thread._local
_threadlocal = threadlocal()
_threadlocal.Hub = None
try:
    _original_fork = os.fork
except AttributeError:
    _original_fork = None
    __all__.remove('fork')


def _switch_helper(function, args, kwargs):
    # work around the fact that greenlet.switch does not support keyword args
    return function(*args, **kwargs)


def spawn_raw(function, *args, **kwargs):
    if kwargs:
        g = greenlet(_switch_helper, get_hub())
        core.active_event(g.switch, function, args, kwargs)
        return g
    else:
        g = greenlet(function, get_hub())
        core.active_event(g.switch, *args)
        return g


def sleep(seconds=0):
    """Put the current greenlet to sleep for at least *seconds*.

    *seconds* may be specified as an integer, or a float if fractional seconds
    are desired. Calling sleep with *seconds* of 0 is the canonical way of
    expressing a cooperative yield.
    """
    unique_mark = object()
    if not seconds >= 0:
        raise IOError(22, 'Invalid argument')
    timer = core.timer(seconds, getcurrent().switch, unique_mark)
    try:
        switch_result = get_hub().switch()
        assert switch_result is unique_mark, 'Invalid switch into sleep(): %r' % (switch_result, )
    except:
        timer.cancel()
        raise


def kill(greenlet, exception=GreenletExit):
    """Kill greenlet asynchronously. The current greenlet is not unscheduled.

    Note, that :meth:`gevent.Greenlet.kill` method does the same and more. However,
    MAIN greenlet - the one that exists initially - does not have ``kill()`` method
    so you have to use this function.
    """
    if not greenlet.dead:
        core.active_event(greenlet.throw, exception)


def _wrap_signal_handler(handler, args, kwargs):
    try:
        handler(*args, **kwargs)
    except:
        core.active_event(MAIN.throw, *sys.exc_info())


def signal(signalnum, handler, *args, **kwargs):
    return core.signal(signalnum, lambda: spawn_raw(_wrap_signal_handler, handler, args, kwargs))


if _original_fork is not None:

    def fork():
        result = _original_fork()
        core.reinit()
        return result


def shutdown():
    """Cancel our CTRL-C handler and wait for core.dispatch() to return."""
    global _threadlocal
    hub = _threadlocal.__dict__.get('hub')
    if hub is not None:
        hub.shutdown()


def get_hub():
    global _threadlocal
    try:
        return _threadlocal.hub
    except AttributeError:
        try:
            hubtype = _threadlocal.Hub
        except AttributeError:
            # do not pretend to support multiple threads because it's not implemented properly by core.pyx
            # this may change in the future, although currently I don't have a strong need for this
            raise NotImplementedError('gevent is only usable from a single thread')
        if hubtype is None:
            hubtype = Hub
        hub = _threadlocal.hub = hubtype()
        return hub

def _socketpair():
    socket = __import__("socket")
    try:
        reader, writer = socket.socketpair()
    except AttributeError:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        client.connect(server.getsockname())
        reader, clientaddr = server.accept()
        writer = client

    writer.setblocking(0)
    reader.setblocking(0)
    return reader, writer

class _Waker(object):
    def __init__(self, cb):
        self.cb = cb
        self.reader, self.writer = _socketpair()
        self.event = core.event(core.EV_READ | core.EV_PERSIST, self.reader.fileno(), self.eventcb)
        self.event.add()

    def eventcb(self, event, evtype):
        import socket
        try:
            self.reader.recv(4096)
        except socket.error:
            pass
        self.cb()

    def wakeup(self):
        self.writer.send(".")

class Hub(greenlet):
    """A greenlet that runs the event loop.

    It is created automatically by :func:`get_hub`.
    """

    def __init__(self):
        greenlet.__init__(self)
        self.keyboard_interrupt_signal = None
        self.waker = None
        self.callqueue = []

    def switch(self):
        cur = getcurrent()
        assert cur is not self, 'Cannot switch to MAINLOOP from MAINLOOP'
        exc_info = sys.exc_info()
        try:
            sys.exc_clear()
            switch_out = getattr(cur, 'switch_out', None)
            if switch_out is not None:
                try:
                    switch_out()
                except:
                    traceback.print_exc()
            return greenlet.switch(self)
        finally:
            core.set_exc_info(*exc_info)

    def run(self):
        global _threadlocal
        assert self is getcurrent(), 'Do not call run() directly'
        try:
            self.keyboard_interrupt_signal = signal(2, core.active_event, MAIN.throw, KeyboardInterrupt)
        except IOError:
            pass  # no signal() on windows

        self.waker = _Waker(self.run_callqueue)
        if self.callqueue:
            self.waker.wakeup()

        try:
            loop_count = 0
            while True:
                try:
                    result = core.dispatch()
                except IOError, ex:
                    loop_count += 1
                    if loop_count > 15:
                        MAIN.throw(*sys.exc_info())
                    sys.stderr.write('Restarting gevent.core.dispatch() after an error [%s]: %s\n' % (loop_count, ex))
                    continue
                raise DispatchExit(result)
                # this function must never return, as it will cause switch() in MAIN to return an unexpected value
        finally:
            if self.keyboard_interrupt_signal is not None:
                self.keyboard_interrupt_signal.cancel()
                self.keyboard_interrupt_signal = None
            if _threadlocal.__dict__.get('hub') is self:
                _threadlocal.__dict__.pop('hub')

    def shutdown(self):
        assert getcurrent() is MAIN, "Shutting down is only possible from MAIN greenlet"
        if self.keyboard_interrupt_signal is not None:
            self.keyboard_interrupt_signal.cancel()
            self.keyboard_interrupt_signal = None
        core.dns_shutdown()
        if not self or self.dead:
            if _threadlocal.__dict__.get('hub') is self:
                _threadlocal.__dict__.pop('hub')
            self.run = None
            return
        try:
            self.switch()
        except DispatchExit, ex:
            if ex.code == 1:  # no more events registered?
                return
            raise

    def call_in_hub(self, f, *args, **kwargs):
        self.callqueue.append((f, args, kwargs))
        if self.waker:
            self.waker.wakeup()

    def run_callqueue(self):
        count = 0
        total = len(self.callqueue)
        for (f, args, kwargs) in self.callqueue:
            try:
                f(*args, **kwargs)
            except:
                pass  # XXX log error

            count += 1
            if count == total:
                break
        del self.callqueue[:count]
        if self.callqueue:
            self.waker.wakeup()


class DispatchExit(Exception):

    def __init__(self, code):
        self.code = code
        Exception.__init__(self, code)


class Waiter(object):
    """A low level communication utility for greenlets.

    Wrapper around greenlet's ``switch()`` and ``throw()`` calls that makes them somewhat safer:

    * switching will occur only if the waiting greenlet is executing :meth:`get` method currently;
    * any error raised in the greenlet is handled inside :meth:`switch` and :meth:`throw`
    * if :meth:`switch`/:meth:`throw` is called before the receiver calls :meth:`get`, then :class:`Waiter`
      will store the value/exception. The following :meth:`get` will return the value/raise the exception.

    The :meth:`switch` and :meth:`throw` methods must only be called from the :class:`Hub` greenlet.
    The :meth:`get` method must be called from a greenlet other than :class:`Hub`.

        >>> result = Waiter()
        >>> _ = core.timer(0.1, result.switch, 'hello from Waiter')
        >>> result.get() # blocks for 0.1 seconds
        'hello from Waiter'

    If switch is called before the greenlet gets a chance to call :meth:`get` then
    :class:`Waiter` stores the value.

        >>> result = Waiter()
        >>> _ = core.timer(0.1, result.switch, 'hi from Waiter')
        >>> sleep(0.2)
        >>> result.get() # returns immediatelly without blocking
        'hi from Waiter'

    .. warning::

        This a limited and dangerous way to communicate between greenlets. It can easily
        leave a greenlet unscheduled forever if used incorrectly. Consider using safer
        :class:`Event`/:class:`AsyncResult`/:class:`Queue` classes.
    """

    __slots__ = ['greenlet', 'value', '_exception']

    def __init__(self):
        self.greenlet = None
        self.value = None
        self._exception = _NONE

    def __str__(self):
        if self._exception is _NONE:
            return '<%s greenlet=%s>' % (type(self).__name__, self.greenlet)
        elif self._exception is None:
            return '<%s greenlet=%s value=%r>' % (type(self).__name__, self.greenlet, self.value)
        else:
            return '<%s greenlet=%s exc_info=%r>' % (type(self).__name__, self.greenlet, self.exc_info)

    def ready(self):
        """Return true if and only if it holds a value or an exception"""
        return self._exception is not _NONE

    def successful(self):
        """Return true if and only if it is ready and holds a value"""
        return self._exception is None

    @property
    def exc_info(self):
        "Holds the exception info passed to :meth:`throw` if :meth:`throw` was called. Otherwise ``None``."
        if self._exception is not _NONE:
            return self._exception

    def switch(self, value=None):
        """Switch to the greenlet if one's available. Otherwise store the value."""
        if self.greenlet is None:
            self.value = value
            self._exception = None
        else:
            assert getcurrent() is get_hub(), "Can only use Waiter.switch method from the Hub greenlet"
            try:
                self.greenlet.switch(value)
            except:
                traceback.print_exc()

    def switch_args(self, *args):
        return self.switch(args)

    def throw(self, *throw_args):
        """Switch to the greenlet with the exception. If there's no greenlet, store the exception."""
        if self.greenlet is None:
            self._exception = throw_args
        else:
            assert getcurrent() is get_hub(), "Can only use Waiter.switch method from the Hub greenlet"
            try:
                self.greenlet.throw(*throw_args)
            except:
                traceback.print_exc()

    def get(self):
        """If a value/an exception is stored, return/raise it. Otherwise until switch() or throw() is called."""
        if self._exception is not _NONE:
            if self._exception is None:
                return self.value
            else:
                getcurrent().throw(*self._exception)
        else:
            assert self.greenlet is None, 'This Waiter is already used by %r' % (self.greenlet, )
            self.greenlet = getcurrent()
            try:
                return get_hub().switch()
            finally:
                self.greenlet = None

    wait = get  # XXX backward compatibility; will be removed in the next release

    # can also have a debugging version, that wraps the value in a tuple (self, value) in switch()
    # and unwraps it in wait() thus checking that switch() was indeed called


class _NONE(object):
    "A special thingy you must never pass to any of gevent API"
    __slots__ = []

    def __repr__(self):
        return '<_NONE>'

_NONE = _NONE()
