import re
import subprocess

from buildbot.process.buildstep import BuildStep, LogLineObserver
from buildbot.process.properties import WithProperties
from buildbot.status.builder import SUCCESS, WARNINGS, FAILURE
from buildbot.steps.shell import ShellCommand, Test
from buildbot.steps.transfer import FileDownload, FileUpload


class PythonDistCommand(ShellCommand):
    """
    Builds a Python dist.
    """
    dist_type = "dist"
    dist_command = "dist"
    use_egg_info = False
    filename_prop = "dist_filename"
    filename_ext = "tar"

    haltOnFailure = True

    filename = None

    def start(self):
        self.command = ["python", "setup.py"]

        if self.use_egg_info:
            props = self.build.getProperties()
            nightly = (str(props.getProperty("nightly")) == "True")

            self.command.append("egg_info")

            if nightly:
                self.command.append("-dR")
            else:
                self.command.append("-Dr")

        self.command.append(self.dist_command)

        ShellCommand.start(self)

    def commandComplete(self, cmd):
        out = cmd.logs['stdio'].getText()
        m = re.search(r'creating \'dist/([A-Za-z0-9_.-]+.%s)\'' %
                      self.filename_ext, out)

        if m:
            self.setFilename(m.group(1))

    def evaluateCommand(self, cmd):
        if cmd.rc != 0 or self.filename is None:
            return FAILURE

        return SUCCESS

    def setFilename(self, filename):
        self.filename = filename
        self.setProperty(self.filename_prop, self.filename,
                         self.__class__.__name__)

    def getText(self, cmd, results):
        if self.filename is not None:
            return ["built", self.filename]

        return ["no %s built" % self.dist_type]

    def describe(self, done=False):
        return ["building %s" % self.dist_type]



class BuildEgg(PythonDistCommand):
    """
    Builds a Python egg.
    """
    dist_type = "egg"
    dist_command = "bdist_egg"
    use_egg_info = True
    filename_prop = "egg_filename"
    filename_ext = "egg"


class BuildSDist(PythonDistCommand):
    """
    Builds a .tar.gz source distribution.
    """
    dist_type = "sdist"
    dist_command = "sdist"
    use_egg_info = True
    filename_prop = "sdist_filename"
    filename_ext = "tar"

    def __init__(self, use_egg_info=False, *args, **kwargs):
        PythonDistCommand.__init__(self, *args, **kwargs)
        self.use_egg_info = use_egg_info

    def commandComplete(self, cmd):
        out = cmd.logs['stdio'].getText()
        m = re.search(r'gzip -f9 dist/([A-Za-z0-9_.-]+.tar)', out)

        if m:
            self.setFilename(m.group(1) + ".gz")


class DownloadLatestBuild(FileDownload):
    """
    Downloads the latest build of a file from the master onto a slave.
    """
    name = "download-latest-build"

    def __init__(self, build_dir, basename, extension, prop_name, **kwargs):
        FileDownload.__init__(self, **kwargs)
        self.addFactoryArguments(build_dir=build_dir,
                                 basename=prefix,
                                 extension=extension,
                                 prop_name=prop_name)
        self.build_dir = build_dir
        self.basename = prefix
        self.extension = extension
        self.prop_name = prop_name

    def describe(self, done=False):
        return "finding latest build for %s" % self.basename

    def start(self):
        recent_build = None
        recent_mtime = 0

        for entry in os.listdir(self.build_dir):
            full_path = os.path.abspath(os.path.join(self.build_dir, entry))

            if (os.path.isfile(full_path) and
                entry.startswith(self.basename) and
                entry.endswith("." + self.extension)):

                time = os.path.getmtime(full_path)

                if time > recent_mtime:
                    recent_mtime = time
                    recent_build = full_path

        if recent_build:
            self.setProperty(self.prop_name, recent_build,
                             "DownloadLatestBuild")
            self.mastersrc = recent_build
            self.slavedest = os.path.join(os.path.abspath(self.slavedest),
                                          os.path.dirname(recent_build))
            return FileDownload.start(self)

        self.step_status.setColor("red")
        self.step_status.setText("build not found")
        self.finished(FAILURE)


class VirtualEnv(ShellCommand):
    """
    Sets up a virtualenv install.
    """
    name = "virtualenv"
    haltOnFailure = True
    description = "Setting up virtualenv"
    descriptionDone = "virtualenv set up"

    def __init__(self, python, *args, **kwargs):
        ShellCommand.__init__(self, *args, **kwargs)
        self.command = [python, "../../virtualenv", "--no-site-packages", "./"]


class EasyInstall(ShellCommand):
    """
    Installs one or more packages using easy_install.
    """
    name = "easy_install"
    haltOnFailure = True
    description = "installing eggs"
    descriptionDone = "eggs installed"

    # Override to specify a custom URL and hosts pattern
    pypi_url = None
    allow_hosts_pattern = None

    def __init__(self, packages, find_links=[], *args, **kwargs):
        ShellCommand.__init__(self, *args, **kwargs)
        self.command = ["easy_install", "--upgrade", "--prefix", "."]

        if self.pypi_url:
            self.command.extend(["-i", self.pypi_url])

        if self.allow_hosts_pattern:
            self.command.extend(["-H", self.allow_hosts_pattern])

        for link in find_links:
            self.command.extend(["--find-links", link])

        self.command.extend(set(packages))


class LocalCommand(ShellCommand):
    """
    Runs a local command on the master.
    """
    name = "local-shell"
    haltOnFailure = True

    def __init__(self, env=None, *args, **kwargs):
        ShellCommand.__init__(self, *args, **kwargs)
        self.env = env

    def start(self):
        properties = self.build.getProperties()
        kwargs = properties.render(self.remote_kwargs)
        kwargs['command'] = properties.render(self.command)
        kwargs['logfiles'] = self.logfiles

        self.step_status.setColor("yellow")
        self.step_status.setText(self.describe(False))

        p = subprocess.Popen(kwargs['command'],
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             shell=False,
                             close_fds=True,
                             universal_newlines=True,
                             env=self.env)

        rc = p.wait()

        stdout_data = p.stdout.read()
        stderr_data = p.stderr.read()

        if stdout_data:
            self.addCompleteLog('stdout', stdout_data)

        if stderr_data:
            self.addCompleteLog('stderr', stderr_data)

        if rc:
            result = FAILURE
        else:
            result = SUCCESS

        self.setStatus(kwargs['command'], result)
        self.finished(result)


class UploadDist(FileUpload):
    """
    Uploads a dist to a remote server.
    """
    name = "upload-dist"
    haltOnFailure = True

    def __init__(self, default_upload_path, dest_filename, *args, **kwargs):
        FileUpload.__init__(self, masterdest="", *args, **kwargs)
        self.default_upload_path = default_upload_path
        self.dest_filename = dest_filename

    def start(self):
        props = self.build.getProperties()
        upload_path = props.getProperty("upload_path")

        if not upload_path:
            upload_path = self.default_upload_path

        self.masterdest = upload_path + "/" + self.dest_filename

        FileUpload.start(self)


class RotateFiles(LocalCommand):
    """
    Rotates files in a directory so the directory doesn't fill up.
    """
    name = "rotate-files"
    description = "Rotating downloadables"
    descriptionDone = "Rotated downloadables"

    def __init__(self, default_directory, patterns, max_files=5,
                 *args, **kwargs):
        LocalCommand.__init__(self, *args, **kwargs)
        self.default_directory = default_directory
        self.patterns = patterns
        self.max_files = max_files

    def start(self):
        props = self.build.getProperties()
        directory = props.getProperty("upload_path")

        if not directory:
            directory = self.default_directory

        patterns = [props.render(pattern) for pattern in self.patterns]
        self.command = ["./scripts/rotate-files.py",
                        directory, "'%s'" % ",".join(patterns),
                        str(self.max_files)]

        LocalCommand.start(self)


class NoseTests(Test):
    flunkOnWarnings = True

    _test_re = re.compile(r'^(.+) \.\.\. (\w+)$')
    _coverage_re = re.compile(
        r'^([A-Za-z0-9_.]+)\s+(\d+)\s+(\d+)\s+(\d+)%\s+([\d, -]+)$')

    def setTestResults(self, total, failed, passed, total_statements,
                       exec_statements):
        Test.setTestResults(self, total=total, failed=failed, passed=passed)

        total_statements += self.step_status.getStatistic("total-statements", 0)
        self.step_status.setStatistic("total-statements", total_statements)

        exec_statements += self.step_status.getStatistic("exec-statements", 0)
        self.step_status.setStatistic("exec-statements", exec_statements)

    def describe(self, done=False):
        description = Test.describe(self, done)

        if done:
            if self.step_status.hasStatistic("total-statements"):
                total_statements = self.step_status.getStatistic("total-statements")
                exec_statements = self.step_status.getStatistic("exec-statements")

                if total_statements > 0:
                    coverage_pct = (float(exec_statements) /
                                    float(total_statements) * 100)
                    description.append('%d%% code coverage (%d / %d statements)'
                                       % (coverage_pct, exec_statements,
                                          total_statements))

        return description

    def evaluateCommand(self, cmd):
        total = 0
        passed = 0
        failed = 0
        rc = cmd.rc

        total_statements = 0
        total_exec_statements = 0

        for line in self.getLog("stdio").getText().split("\n"):
            line = line.strip()

            m = self._test_re.search(line)

            if m:
                testname, result = m.groups()
                total += 1

                if result == "ok":
                    passed += 1
                else:
                    failed += 1
            else:
                m = self._coverage_re.search(line)

                if m:
                    package, statements, exec_statements, coverage, missing = \
                        m.groups()

                    total_statements += int(statements)
                    total_exec_statements += int(exec_statements)

        self.setTestResults(total=total, failed=failed, passed=passed,
                            total_statements=total_statements,
                            exec_statements=total_exec_statements)

        if failed:
            rc = FAILURE

        return rc
