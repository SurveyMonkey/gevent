class wrap_errors(object):
    """Helper to make function return an exception, rather than raise it.

    Because every exception that is unhandled by greenlet will be logged by the hub,
    it is desirable to prevent non-error exceptions from leaving a greenlet.
    This can done with simple try/except construct:

    def func1(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (A, B, C), ex:
            return ex

    wrap_errors provides a shortcut to write that in one line:

    func1 = wrap_errors((A, B, C), func)

    It also preserves __str__ and __repr__ of the original function.
    """

    def __init__(self, errors, func):
        """Make a new function from `func', such that it catches `errors' (an
        Exception subclass, or a tuple of Exception subclasses) and return
        it as a value.
        """
        self.errors = errors
        self.func = func

    def __call__(self, *args, **kwargs):
        try:
            return self.func(*args, **kwargs)
        except self.errors, ex:
            return ex

    def __str__(self):
        return str(self.func)

    def __repr__(self):
        return repr(self.func)

    def __getattr__(self, item):
        return getattr(self.func, item)

