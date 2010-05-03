#!/usr/bin/env python
"""
gevent build & installation script
----------------------------------

If you have more than one libevent installed or it is installed in a
non-standard location, use the options to point to the right dirs:

    -Idir      add include dir
    -Ldir      add library dir
    -1         prefer libevent1
    -2         prefer libevent2
    --static   link libevent statically (default on win32)
    --dynamic  link libevent dynamically (default on any platform other than win32)

Also,

    setup.py build --libevent DIR

is a shortcut for

    setup.py build -IDIR -IDIR/include LDIR/.libs

"""

# XXX Options --static and --dynamic aren't tested for values other than theirs
#     defaults (that is, --static on win32, --dynamic on everything else)

import sys
import os
import re
import time
from distutils.core import Extension, setup, Command
from distutils.command import build_ext, config

from os.path import join, split, exists, isdir, abspath, basename


__version__ = re.search("__version__\s*=\s*'(.*)'", open('gevent/__init__.py').read(), re.M).group(1)
assert __version__


include_dirs = []                 # specified by -I
library_dirs = []                 # specified by -L
libevent_source_path = None       # specified by --libevent
VERBOSE = '-v' in sys.argv
static = sys.platform == 'win32'  # set to True with --static; set to False with --dynamic
extra_compile_args = []
sources = ['gevent/core.c']
libraries = []
extra_objects = []

class build_libevent(Command):
    description = "download and compile libevent"
    user_options=[]
    url = "http://www.monkey.org/~provos/libevent-1.4.13-stable.tar.gz"
    digest = "0b3ea18c634072d12b3c1ee734263664"
    basename = "libevent-1.4.13-stable"

    def finalize_options(self):
        pass
    def initialize_options(self):
        pass
    def run(self):
        import urllib, tarfile
        try:
            from hashlib import md5
        except ImportError:
            from md5 import md5
        url = self.url

        fn = url.split("/")[-1]
        dirname = fn[:-len(".tar.gz")]

        if os.path.exists(fn):
            pass
        else:
            print "downloading libevent source from", url
            tgz = urllib.urlopen(url).read()
            digest = md5(tgz).hexdigest()
            if digest!=self.digest:
                sys.exit("wrong md5 sum")
            print "md5 sum ok"
            open(fn, "wb").write(tgz)

        tf = tarfile.open(fn,'r:gz')
        tf.extractall(".")
        addlibs = []

        cwd = os.getcwd()
        os.chdir(dirname)
        try:
            if not os.path.exists("./config.status"):
                os.system("./configure --with-pic --disable-shared")
            os.system("make")
            for line in open("Makefile"):
                if line.startswith("LIBS = "):
                    addlibs = [x[2:] for x in line[len("LIBS = "):].strip().split() if x.startswith("-l")]
        finally:
            os.chdir(cwd)


        config = self.distribution.reinitialize_command('config')
        config.include_dirs = [self.basename, "%s/include" % self.basename]
        config.library_dirs = ["%s/.libs" % self.basename]
        config.libraries = addlibs

class build_libevent2(build_libevent):
    url = "http://monkey.org/~provos/libevent-2.0.4-alpha.tar.gz"
    digest = "dbc50f32af9f2ade151a0737e5edf205"
    basename="libevent-2.0.4-alpha"

class my_config(config.config):
    def run(self):
        version = None

        # both libevent2 and libevent1 install event.h.
        if self.check_header("event.h"):
            if self.search_cpp(".*GEVENT_USING_LIBEVENT_2.*", """
#include <event.h>
#if _EVENT_NUMERIC_VERSION >= 0x02000000
GEVENT_USING_LIBEVENT_2
#endif
"""):
                version = 2
            else:
                version = 1

        if not version: # compat headers not installed? XXX does this make sense?
            if self.check_header("event2/event.h"):
                version = 2

        if not version:
            raise RuntimeError("libevent headers not found")

        if not self.check_lib("event"):
            raise RuntimeError("cannot link with libevent")

        if self.check_func("evbuffer_get_length", libraries=["event"], decl=1, call=1):
            linker_version = 2
        else:
            linker_version = 1

        if linker_version != version:
            raise RuntimeError("version mismatch detected: include files for libevent %s found , but linking against libevent %s" % (version, linker_version))

        build = self.distribution.reinitialize_command('build_ext')
        build.define = 'USE_LIBEVENT_%s' % version
        build.include_dirs = self.include_dirs
        build.library_dirs = self.library_dirs
        build.libraries = self.libraries

# hack: create a symlink from build/../core.so to gevent/core.so to prevent "ImportError: cannot import name core" failures

class my_build_ext(build_ext.build_ext):
    def finalize_options(self):
        try:
            self.run_command('config')
        except RuntimeError, err:
            print "\ngevent setup.py: could not find a working libevent installation: %s" % (err, )
            print "setup.py will download libevent and compile it in 5 seconds (hit CTRL-C to abort)\n"
            time.sleep(5)

            self.run_command("build_libevent")
            self.run_command('config')

        build_ext.build_ext.finalize_options(self)

    def build_extension(self, ext):
        result = build_ext.build_ext.build_extension(self, ext)
        if self.inplace:
            return result

        fullname = self.get_ext_fullname(ext.name)
        modpath = fullname.split('.')
        filename = self.get_ext_filename(ext.name)
        filename = split(filename)[-1]
        filename = join(*modpath[:-1] + [filename])
        path_to_build_core_so = abspath(join(self.build_lib, filename))
        path_to_core_so = abspath(join('gevent', basename(path_to_build_core_so)))
        if path_to_build_core_so != path_to_core_so:
            print 'Linking %s to %s' % (path_to_build_core_so, path_to_core_so)
            try:
                os.unlink(path_to_core_so)
            except OSError:
                pass
            if hasattr(os, 'symlink'):
                os.symlink(path_to_build_core_so, path_to_core_so)
            else:
                import shutil
                shutil.copyfile(path_to_build_core_so, path_to_core_so)

        return result

def check_dir(path, must_exist):
    if not isdir(path):
        msg = 'Not a directory: %s' % path
        if must_exist:
            sys.exit(msg)


def add_include_dir(path, must_exist=True):
    if path not in include_dirs:
        check_dir(path, must_exist)
        include_dirs.append(path)


def add_library_dir(path, must_exist=True):
    if path not in library_dirs:
        check_dir(path, must_exist)
        library_dirs.append(path)

# parse options: -I NAME / -INAME / -L NAME / -LNAME / -1 / -2 / --libevent DIR / --static / --dynamic
# we're cutting out options from sys.path instead of using optparse
# so that these option can co-exists with distutils' options
i = 1
while i < len(sys.argv):
    arg = sys.argv[i]
    if arg == '--libevent':
        del sys.argv[i]
        libevent_source_path = sys.argv[i]
        add_include_dir(join(libevent_source_path, 'include'), must_exist=False)
        add_include_dir(libevent_source_path, must_exist=False)
        add_library_dir(join(libevent_source_path, '.libs'), must_exist=False)
        if sys.platform == 'win32':
            add_include_dir(join(libevent_source_path, 'compat'), must_exist=False)
            add_include_dir(join(libevent_source_path, 'WIN32-Code'), must_exist=False)
    elif arg == '--static':
        static = True
    elif arg == '--dynamic':
        static = False
    else:
        i = i+1
        continue
    del sys.argv[i]


if not sys.argv[1:] or  '--help' in ' '.join(sys.argv):
    print __doc__
else:
    if static:
        if not libevent_source_path:
            sys.exit('Please provide path to libevent source with --libevent DIR')
        extra_compile_args += ['-DHAVE_CONFIG_H']
        libevent_sources = ['event.c',
                            'buffer.c',
                            'evbuffer.c',
                            'event_tagging.c',
                            'evutil.c',
                            'log.c',
                            'signal.c',
                            'evdns.c',
                            'http.c',
                            'strlcpy.c']
        if sys.platform == 'win32':
            libraries = ['wsock32', 'advapi32']
            include_dirs.extend([ join(libevent_source_path, 'WIN32-Code'),
                                  join(libevent_source_path, 'compat') ])
            libevent_sources.append('WIN32-Code/win32.c')
            extra_compile_args += ['-DWIN32']
        else:
            libevent_sources += ['select.c']
            print 'XXX --static is not well supported on non-win32 platforms: only select is enabled'
        for filename in libevent_sources:
            sources.append( join(libevent_source_path, filename) )
    else:
        libraries = ['event']


gevent_core = Extension(name='gevent.core',
                        sources=sources,
                        include_dirs=include_dirs,
                        library_dirs=library_dirs,
                        libraries=libraries,
                        extra_objects=extra_objects,
                        extra_compile_args=extra_compile_args)


if __name__ == '__main__':
    setup(
        name='gevent',
        version=__version__,
        description='Python network library that uses greenlet and libevent for easy and scalable concurrency',
        author='Denis Bilenko',
        author_email='denis.bilenko@gmail.com',
        url='http://www.gevent.org/',
        packages=['gevent'],
        ext_modules=[gevent_core],
        cmdclass=dict(build_ext=my_build_ext, config=my_config,
                      build_libevent=build_libevent,  build_libevent1=build_libevent,
                      build_libevent2=build_libevent2),
        classifiers=[
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: POSIX",
        "Operating System :: Microsoft :: Windows",
        "Topic :: Internet",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Intended Audience :: Developers",
        "Development Status :: 4 - Beta"]
        )

