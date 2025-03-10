# -*- encoding: utf-8 -*-

import logging
import os
import re
import shutil
import subprocess
import sys

import pytest
import yaml
from click.testing import CliRunner

from cekit.cli import cli
from cekit.tools import Chdir
from cekit.version import __version__

image_descriptor = {
    "schema_version": 1,
    "from": "centos:7",
    "name": "test/image",
    "version": "1.0",
    "labels": [{"name": "foo", "value": "bar"}, {"name": "labela", "value": "a"}],
    "osbs": {"repository": {"name": "repo", "branch": "branch"}},
}


def run_cekit(
    cwd, parameters=["build", "--dry-run", "docker"], message=None, return_code=0
):
    with Chdir(cwd):
        result = CliRunner().invoke(cli, parameters, catch_exceptions=False)
        sys.stdout.write("\n")
        sys.stdout.write(result.output)

        assert result.exit_code == return_code

        if message:
            assert message in result.output

        return result


def run_osbs(
    descriptor,
    image_dir,
    mocker,
    return_code=0,
    build_command=None,
    general_command=None,
):
    if build_command is None:
        build_command = ["build", "osbs"]

    if general_command is None:
        general_command = ["--redhat"]

    # We are mocking it, so do not require it at test time
    mocker.patch("cekit.builders.osbs.OSBSBuilder.dependencies", return_value={})
    mocker.patch("cekit.builders.osbs.OSBSBuilder._wait_for_osbs_task")
    mocker.patch("cekit.builders.osbs.DistGit.prepare")

    mocker.patch.object(
        subprocess,
        "check_output",
        side_effect=[
            b"true",  # git rev-parse --is-inside-work-tree
            b"/home/repos/path",  # git rev-parse --show-toplevel
            b"branch",  # git rev-parse --abbrev-ref HEAD
            b"3b9283cb26b35511517ff5c0c3e11f490cba8feb",  # git rev-parse HEAD
            b"",  # git ls-files .
            b"",  # git ls-files --others --exclude-standard
            b"",  # git diff-files --name-only
            b"ssh://someuser@somehost.com/containers/somerepo",  # git config --get remote.origin.url
            b"3b9283cb26b35511517ff5c0c3e11f490cba8feb",  # git rev-parse HEAD
            b"1234",  # brew call --python...
            b"UUU",
        ],
    )

    with open(os.path.join(image_dir, "image.yaml"), "w") as fd:
        yaml.dump(descriptor, fd, default_flow_style=False)

    return run_cekit(
        image_dir,
        general_command
        + ["-v", "--work-dir", image_dir, "--config", "config"]
        + build_command,
        return_code=return_code,
    )


def test_osbs_builder_with_assume_yes(tmpdir, mocker, caplog):
    caplog.set_level(logging.DEBUG, logger="cekit")

    # Specifically set the decision result to False, to fail any build
    # that depends on the decision. But in case the --assume-yes switch is used
    # we should not get to this point at all. If we get, the test should fail.
    mock_decision = mocker.patch("cekit.tools.decision", return_value=False)
    mock_check_call = mocker.patch.object(subprocess, "check_call")
    mocker.patch.object(subprocess, "call", return_value=1)

    source_dir = tmpdir.mkdir("source")
    source_dir.mkdir("osbs").mkdir("repo")

    run_osbs(
        image_descriptor.copy(),
        str(source_dir),
        mocker,
        0,
        ["build", "osbs", "--assume-yes"],
    )

    mock_decision.assert_not_called()

    mock_check_call.assert_has_calls(
        [
            mocker.call(["git", "add", "--all", "Dockerfile"]),
            mocker.call(
                [
                    "git",
                    "commit",
                    "-q",
                    "-m",
                    "Sync with path, commit 3b9283cb26b35511517ff5c0c3e11f490cba8feb",
                ]
            ),
            mocker.call(["git", "push", "-q", "origin", "branch"]),
        ]
    )

    assert (
        "Committing with message: 'Sync with path, commit 3b9283cb26b35511517ff5c0c3e11f490cba8feb'"
        in caplog.text
    )
    assert "Image was built successfully in OSBS!" in caplog.text


def test_osbs_builder_with_process_error(tmpdir, mocker, caplog):
    caplog.set_level(logging.DEBUG, logger="cekit")

    # Specifically set the decision result to False, to fail any build
    # that depends on the decision. But in case the --assume-yes switch is used
    # we should not get to this point at all. If we get, the test should fail.
    mock_decision = mocker.patch("cekit.tools.decision", return_value=False)
    mock_check_call = mocker.patch.object(subprocess, "check_call")
    mock_check_call.side_effect = [
        0,
        subprocess.CalledProcessError(1, "git", output="A GIT ERROR"),
    ]
    mocker.patch.object(subprocess, "call", return_value=1)

    source_dir = tmpdir.mkdir("source")
    source_dir.mkdir("osbs").mkdir("repo")

    run_osbs(
        image_descriptor.copy(),
        str(source_dir),
        mocker,
        1,
        ["build", "osbs", "--assume-yes"],
    )

    mock_decision.assert_not_called()

    assert "A GIT ERROR" in caplog.text
    assert "Image was built successfully in OSBS!" not in caplog.text


def test_osbs_builder_with_push_with_sync_only(tmpdir, mocker, caplog):
    """
    Should sync with dist-git repository without kicking the build
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    source_dir = tmpdir.mkdir("source")
    repo_dir = source_dir.mkdir("osbs").mkdir("repo")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "call", return_value=1)

    mock_check_call = mocker.patch.object(subprocess, "check_call")

    descriptor = image_descriptor.copy()

    run_osbs(descriptor, str(source_dir), mocker, 0, ["build", "osbs", "--sync-only"])

    assert os.path.exists(str(repo_dir.join("Dockerfile"))) is True

    mock_check_call.assert_has_calls(
        [
            mocker.call(["git", "add", "--all", "Dockerfile"]),
            mocker.call(
                [
                    "git",
                    "commit",
                    "-q",
                    "-m",
                    "Sync with path, commit 3b9283cb26b35511517ff5c0c3e11f490cba8feb",
                ]
            ),
            mocker.call(["git", "push", "-q", "origin", "branch"]),
        ]
    )

    assert (
        "Committing with message: 'Sync with path, commit 3b9283cb26b35511517ff5c0c3e11f490cba8feb'"
        in caplog.text
    )
    assert (
        "The --sync-only parameter was specified, build will not be executed, exiting"
        in caplog.text
    )


def test_osbs_builder_kick_build_without_push(tmpdir, mocker, caplog):
    """
    Does not push sources to dist-git. This is the case when the
    generated files are the same as already existing in dist-git
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "call", return_value=0)

    source_dir = tmpdir.mkdir("source")
    repo_dir = source_dir.mkdir("osbs").mkdir("repo")

    mock_check_call = mocker.patch.object(subprocess, "check_call")

    descriptor = image_descriptor.copy()

    run_osbs(descriptor, str(source_dir), mocker)

    assert os.path.exists(str(repo_dir.join("Dockerfile"))) is True

    mock_check_call.assert_has_calls(
        [
            mocker.call(["git", "add", "--all", "Dockerfile"]),
        ]
    )

    assert "No changes made to the code, committing skipped" in caplog.text
    assert "Image was built successfully in OSBS!" in caplog.text


def test_osbs_builder_kick_build_with_push(tmpdir, mocker, caplog):
    """
    Does not push sources to dist-git. This is the case when the
    generated files are the same as already existing in dist-git
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    source_dir = tmpdir.mkdir("source")
    repo_dir = source_dir.mkdir("osbs").mkdir("repo")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "call", return_value=1)

    mock_check_call = mocker.patch.object(subprocess, "check_call")

    descriptor = image_descriptor.copy()

    run_osbs(descriptor, str(source_dir), mocker)

    assert os.path.exists(str(repo_dir.join("Dockerfile"))) is True

    mock_check_call.assert_has_calls(
        [
            mocker.call(["git", "add", "--all", "Dockerfile"]),
            mocker.call(
                [
                    "git",
                    "commit",
                    "-q",
                    "-m",
                    "Sync with path, commit 3b9283cb26b35511517ff5c0c3e11f490cba8feb",
                ]
            ),
            mocker.call(["git", "push", "-q", "origin", "branch"]),
        ]
    )

    assert (
        "Committing with message: 'Sync with path, commit 3b9283cb26b35511517ff5c0c3e11f490cba8feb'"
        in caplog.text
    )
    assert "Image was built successfully in OSBS!" in caplog.text


# https://github.com/cekit/cekit/issues/504
def test_osbs_builder_add_help_file(tmpdir, mocker, caplog):
    """
    Checks if help.md file is generated and added to dist-git
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)

    source_dir = tmpdir.mkdir("source")
    repo_dir = source_dir.mkdir("osbs").mkdir("repo")

    mocker.patch.object(subprocess, "call", return_value=0)
    mock_check_call = mocker.patch.object(subprocess, "check_call")

    descriptor = image_descriptor.copy()
    descriptor["help"] = {"add": True}

    run_osbs(descriptor, str(source_dir), mocker)

    assert os.path.exists(str(repo_dir.join("Dockerfile"))) is True
    assert os.path.exists(str(repo_dir.join("help.md"))) is True

    calls = [
        mocker.call(["git", "add", "--all", "Dockerfile"]),
        mocker.call(["git", "add", "--all", "help.md"]),
    ]

    mock_check_call.assert_has_calls(calls, any_order=True)

    assert len(mock_check_call.mock_calls) == len(calls)
    assert "Image was built successfully in OSBS!" in caplog.text


# https://github.com/cekit/cekit/issues/394
def test_osbs_builder_add_extra_files(tmpdir, mocker, caplog):
    """
    Checks if content of the 'osbs_extra' directory content is copied to dist-git
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)

    source_dir = tmpdir.mkdir("source")
    repo_dir = source_dir.mkdir("osbs").mkdir("repo")
    dist_dir = source_dir.mkdir("osbs_extra")

    dist_dir.join("file_a").write_text("Some content", "utf-8")
    dist_dir.join("file_b").write_text("Some content", "utf-8")
    dist_dir.mkdir("child").join("other").write_text("Some content", "utf-8")

    os.symlink("/etc", str(dist_dir.join("a_symlink")))

    mocker.patch.object(subprocess, "call", return_value=0)
    mock_check_call = mocker.patch.object(subprocess, "check_call")

    run_osbs(image_descriptor, str(source_dir), mocker)

    assert os.path.exists(str(repo_dir.join("Dockerfile"))) is True
    assert os.path.exists(str(repo_dir.join("osbs_extra", "file_a"))) is True
    assert os.path.exists(str(repo_dir.join("osbs_extra", "file_b"))) is True

    calls = [
        mocker.call(["git", "add", "--all", "osbs_extra"]),
        mocker.call(["git", "add", "--all", "Dockerfile"]),
    ]

    mock_check_call.assert_has_calls(calls, any_order=True)

    assert os.path.exists(str(repo_dir.join("osbs_extra", "file_b"))) is True
    assert len(mock_check_call.mock_calls) == len(calls)
    assert "Image was built successfully in OSBS!" in caplog.text
    assert (
        "Copying files to dist-git '{}' directory".format(str(repo_dir)) in caplog.text
    )
    assert (
        "Copying 'target/image/osbs_extra' to '{}'...".format(
            os.path.join(str(repo_dir), "osbs_extra")
        )
        in caplog.text
    )
    assert "Staging 'osbs_extra'..." in caplog.text


def test_osbs_builder_add_extra_files_with_extra_dir_target(tmpdir, mocker, caplog):
    """
    Checks if content of the 'osbs_extra' directory content is copied to dist-git and embedded in Dockerfile
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)

    source_dir = tmpdir.mkdir("source")
    repo_dir = source_dir.mkdir("osbs").mkdir("repo")
    dist_dir = source_dir.mkdir("osbs_extra")
    repo_dir_osbs_extra = repo_dir.mkdir("osbs_extra")
    repo_dir_osbs_extra.mkdir("foobar_original")
    repo_dir_osbs_extra.join("config_original.yaml").write_text("Some content", "utf-8")

    dist_dir.join("file_a").write_text("Some content", "utf-8")
    dist_dir.join("file_b").write_text("Some content", "utf-8")
    dist_dir.mkdir("child").join("other").write_text("Some content", "utf-8")

    os.symlink("/etc", str(dist_dir.join("a_symlink")))

    mocker.patch.object(subprocess, "call", return_value=0)
    mock_check_call = mocker.patch.object(subprocess, "check_call")

    overrides_descriptor = {
        "schema_version": 1,
        "osbs": {"extra_dir_target": "/foobar"},
    }

    with open(os.path.join(str(source_dir), "overrides.yaml"), "w") as fd:
        yaml.dump(overrides_descriptor, fd, default_flow_style=False)

    run_osbs(
        image_descriptor,
        str(source_dir),
        mocker,
        build_command=["build", "--overrides-file", "overrides.yaml", "osbs"],
    )

    assert os.path.exists(str(repo_dir.join("Dockerfile"))) is True
    assert os.path.exists(str(repo_dir.join("osbs_extra").join("file_a"))) is True
    assert os.path.exists(str(repo_dir.join("osbs_extra").join("file_b"))) is True

    calls = [
        mocker.call(["git", "rm", "-rf", "osbs_extra"]),
        mocker.call(["git", "add", "--all", "osbs_extra"]),
        mocker.call(["git", "add", "--all", "Dockerfile"]),
    ]

    mock_check_call.assert_has_calls(calls, any_order=True)

    assert os.path.exists(str(repo_dir.join("osbs_extra").join("file_b"))) is True
    assert len(mock_check_call.mock_calls) == len(calls)
    assert "Image was built successfully in OSBS!" in caplog.text
    assert (
        "Copying files to dist-git '{}' directory".format(str(repo_dir)) in caplog.text
    )
    assert "Removing old osbs extra directory : osbs_extra" in caplog.text
    assert (
        "Copying 'target/image/osbs_extra' to '{}'...".format(
            os.path.join(str(repo_dir), "osbs_extra")
        )
        in caplog.text
    )
    assert "Staging 'osbs_extra'..." in caplog.text
    with open(os.path.join(str(repo_dir), "Dockerfile"), "r") as _file:
        dockerfile = _file.read()
        assert (
            """## START target image test/image:1.0
## \\
    FROM centos:7

    COPY osbs_extra /foobar

    USER root
"""
            in dockerfile
        )

    assert "COPY osbs_extra /foobar" in dockerfile


def test_osbs_builder_add_extra_files_non_default_with_extra_dir_target(
    tmpdir, mocker, caplog
):
    """
    Checks if content of the 'osbs_extra' directory content is copied to dist-git and embedded in Dockerfile
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)

    source_dir = tmpdir.mkdir("source")
    repo_dir = source_dir.mkdir("osbs").mkdir("repo")
    dist_dir = source_dir.mkdir("foobar")
    repo_dir.mkdir("foobar")

    dist_dir.join("file_a").write_text("Some content", "utf-8")
    dist_dir.join("file_b").write_text("Some content", "utf-8")
    dist_dir.mkdir("child").join("other").write_text("Some content", "utf-8")

    os.symlink("/etc", str(dist_dir.join("a_symlink")))

    mocker.patch.object(subprocess, "call", return_value=0)
    mock_check_call = mocker.patch.object(subprocess, "check_call")

    overrides_descriptor = {
        "schema_version": 1,
        "osbs": {"extra_dir_target": "/", "extra_dir": "foobar"},
    }

    with open(os.path.join(str(source_dir), "overrides.yaml"), "w") as fd:
        yaml.dump(overrides_descriptor, fd, default_flow_style=False)

    run_osbs(
        image_descriptor,
        str(source_dir),
        mocker,
        build_command=["build", "--overrides-file", "overrides.yaml", "osbs"],
    )

    assert os.path.exists(str(repo_dir.join("Dockerfile"))) is True
    assert os.path.exists(str(repo_dir.join("foobar").join("file_a"))) is True
    assert os.path.exists(str(repo_dir.join("foobar").join("file_b"))) is True

    calls = [
        mocker.call(["git", "rm", "-rf", "foobar"]),
        mocker.call(["git", "add", "--all", "foobar"]),
        mocker.call(["git", "add", "--all", "Dockerfile"]),
    ]

    mock_check_call.assert_has_calls(calls, any_order=True)

    assert os.path.exists(str(repo_dir.join("foobar").join("file_b"))) is True
    assert len(mock_check_call.mock_calls) == len(calls)
    assert "Image was built successfully in OSBS!" in caplog.text
    assert (
        "Copying files to dist-git '{}' directory".format(str(repo_dir)) in caplog.text
    )
    assert "Removing old osbs extra directory : foobar" in caplog.text
    assert (
        "Copying 'target/image/foobar' to '{}'...".format(
            os.path.join(str(repo_dir), "foobar")
        )
        in caplog.text
    )
    assert "Staging 'foobar'..." in caplog.text
    with open(os.path.join(str(repo_dir), "Dockerfile"), "r") as _file:
        dockerfile = _file.read()

    assert "COPY foobar /" in dockerfile


def test_osbs_builder_add_extra_files_and_overwrite(tmpdir, mocker, caplog):
    """
    Checks if content of the 'osbs_extra' directory content is copied to dist-git
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)

    source_dir = tmpdir.mkdir("source")
    repo_dir = source_dir.mkdir("osbs").mkdir("repo")
    repo_dir.mkdir("osbs_extra").mkdir("child").join("other").write_text(
        "Some content", "utf-8"
    )

    dist_dir = source_dir.mkdir("osbs_extra")

    dist_dir.join("file_a").write_text("Some content", "utf-8")
    dist_dir.join("file_b").write_text("Some content", "utf-8")
    dist_dir.mkdir("child").join("other").write_text("Some content", "utf-8")

    os.symlink("/etc", str(dist_dir.join("a_symlink")))

    mocker.patch.object(subprocess, "call", return_value=0)
    mock_check_call = mocker.patch.object(subprocess, "check_call")

    run_osbs(image_descriptor, str(source_dir), mocker)

    assert os.path.exists(str(repo_dir.join("Dockerfile"))) is True
    assert os.path.exists(str(repo_dir.join("osbs_extra", "file_a"))) is True
    assert os.path.exists(str(repo_dir.join("osbs_extra", "file_b"))) is True
    assert os.path.exists(str(repo_dir.join("osbs_extra", "child"))) is True
    assert os.path.exists(str(repo_dir.join("osbs_extra", "child", "other"))) is True

    calls = [
        mocker.call(["git", "rm", "-rf", "osbs_extra"]),
        mocker.call(["git", "add", "--all", "osbs_extra"]),
        mocker.call(["git", "add", "--all", "Dockerfile"]),
    ]

    mock_check_call.assert_has_calls(calls, any_order=True)

    assert len(mock_check_call.mock_calls) == len(calls)
    assert "Image was built successfully in OSBS!" in caplog.text
    assert (
        "Copying files to dist-git '{}' directory".format(str(repo_dir)) in caplog.text
    )
    assert (
        "Copying 'target/image/osbs_extra' to '{}'...".format(
            os.path.join(str(repo_dir), "osbs_extra")
        )
        in caplog.text
    )
    assert "Staging 'osbs_extra'..." in caplog.text


# https://github.com/cekit/cekit/issues/394
def test_osbs_builder_add_extra_files_from_custom_dir(tmpdir, mocker, caplog):
    """
    Checks if content of the custom specified 'dist' directory content is copied to dist-git
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)

    source_dir = tmpdir.mkdir("source")
    repo_dir = source_dir.mkdir("osbs").mkdir("repo")
    dist_dir = source_dir.mkdir("dist")

    dist_dir.join("file_a").write_text("Some content", "utf-8")
    dist_dir.join("file_b").write_text("Some content", "utf-8")
    dist_dir.mkdir("child").join("other").write_text("Some content", "utf-8")

    os.symlink("/etc", str(dist_dir.join("a_symlink")))

    mocker.patch.object(subprocess, "call", return_value=0)
    mock_check_call = mocker.patch.object(subprocess, "check_call")

    descriptor = image_descriptor.copy()

    descriptor["osbs"]["extra_dir"] = "dist"

    run_osbs(descriptor, str(source_dir), mocker)

    assert os.path.exists(str(repo_dir.join("Dockerfile"))) is True
    assert os.path.exists(str(repo_dir.join("dist").join("file_a"))) is True
    assert os.path.exists(str(repo_dir.join("dist").join("file_b"))) is True

    calls = [
        mocker.call(["git", "add", "--all", "dist"]),
        mocker.call(["git", "add", "--all", "Dockerfile"]),
    ]

    mock_check_call.assert_has_calls(calls, any_order=True)

    assert len(mock_check_call.mock_calls) == len(calls)
    assert "Image was built successfully in OSBS!" in caplog.text
    assert (
        "Copying files to dist-git '{}' directory".format(str(repo_dir)) in caplog.text
    )
    assert (
        "Copying 'target/image/dist' to '{}'...".format(
            os.path.join(str(repo_dir), "dist")
        )
        in caplog.text
    )
    assert "Staging 'dist'..." in caplog.text
    with open(os.path.join(str(repo_dir), "Dockerfile"), "r") as _file:
        dockerfile = _file.read()

    assert "COPY foobar /" not in dockerfile


# https://github.com/cekit/cekit/issues/542
def test_osbs_builder_extra_default(tmpdir, mocker, caplog):
    caplog.set_level(logging.DEBUG, logger="cekit")

    source_dir = tmpdir.mkdir("source")

    mocker.patch.object(subprocess, "call", return_value=0)
    mocker.patch.object(subprocess, "check_call")

    shutil.copytree(
        os.path.join(os.path.dirname(__file__), "modules"),
        os.path.join(str(source_dir), "tests", "modules"),
    )

    descriptor = image_descriptor.copy()

    del descriptor["osbs"]

    run_osbs(descriptor, str(source_dir), mocker, return_code=1)

    with open(os.path.join(str(source_dir), "target", "image.yaml"), "r") as _file:
        effective = yaml.safe_load(_file)

    assert effective["osbs"] is not None
    assert effective["osbs"]["extra_dir"] == "osbs_extra"


def test_osbs_builder_add_files_to_dist_git_when_it_is_a_directory(
    tmpdir, mocker, caplog
):
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "call")
    mock_check_call = mocker.patch.object(subprocess, "check_call")

    res = mocker.Mock()
    res.getcode.return_value = 200
    res.read.side_effect = [b"test", None]

    mocker.patch("cekit.tools.urlopen", return_value=res)

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [{"path": "manifests", "dest": "/manifests"}]

    tmpdir.mkdir("osbs").mkdir("repo").mkdir(".git").join("other").write_text(
        "Some content", "utf-8"
    )

    tmpdir.mkdir("manifests")

    with open(
        os.path.join(str(tmpdir), "manifests", "some-manifest-file.yaml"), "w"
    ) as _file:
        _file.write("CONTENT")

    run_osbs(descriptor, str(tmpdir), mocker)

    calls = [
        mocker.call(["git", "push", "-q", "origin", "branch"]),
        mocker.call(
            [
                "git",
                "commit",
                "-q",
                "-m",
                "Sync with path, commit 3b9283cb26b35511517ff5c0c3e11f490cba8feb",
            ]
        ),
        mocker.call(["git", "add", "--all", "Dockerfile"]),
        mocker.call(["git", "add", "--all", "manifests"]),
    ]

    mock_check_call.assert_has_calls(calls, any_order=True)
    assert len(mock_check_call.mock_calls) == len(calls)
    assert "Skipping '.git' directory" in caplog.text
    assert "Staging 'manifests'..." in caplog.text


def test_osbs_builder_add_artifact_directory_to_dist_git_when_it_already_exists(
    tmpdir, mocker, caplog
):
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "call")
    mock_check_call = mocker.patch.object(subprocess, "check_call")

    res = mocker.Mock()
    res.getcode.return_value = 200
    res.read.side_effect = [b"test", None]

    mocker.patch("cekit.tools.urlopen", return_value=res)

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [{"path": "manifests", "dest": "/manifests"}]

    tmpdir.mkdir("osbs").mkdir("repo").mkdir(".git").join("other").write_text(
        "Some content", "utf-8"
    )

    tmpdir.join("osbs").join("repo").mkdir("manifests").join(
        "old-manifests.yaml"
    ).write_text("Some content", "utf-8")

    tmpdir.mkdir("manifests")

    with open(
        os.path.join(str(tmpdir), "manifests", "some-manifest-file.yaml"), "w"
    ) as _file:
        _file.write("CONTENT")

    run_osbs(descriptor, str(tmpdir), mocker)

    calls = [
        mocker.call(["git", "push", "-q", "origin", "branch"]),
        mocker.call(
            [
                "git",
                "commit",
                "-q",
                "-m",
                "Sync with path, commit 3b9283cb26b35511517ff5c0c3e11f490cba8feb",
            ]
        ),
        mocker.call(["git", "add", "--all", "Dockerfile"]),
        mocker.call(["git", "add", "--all", "manifests"]),
    ]

    mock_check_call.assert_has_calls(calls, any_order=True)
    assert len(mock_check_call.mock_calls) == len(calls)
    assert "Skipping '.git' directory" in caplog.text
    assert "Staging 'manifests'..." in caplog.text
    assert "Removing old 'manifests' directory" in caplog.text


def test_osbs_builder_add_files_to_dist_git_without_dotgit_directory(
    tmpdir, mocker, caplog
):
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "call")
    mock_check_call = mocker.patch.object(subprocess, "check_call")

    res = mocker.Mock()
    res.getcode.return_value = 200
    res.read.side_effect = [b"test", None]
    res.getheader.return_value = 0

    mocker.patch("cekit.tools.urlopen", return_value=res)

    (
        tmpdir.mkdir("osbs")
        .mkdir("repo")
        .mkdir(".git")
        .join("other")
        .write_text("Some content", "utf-8")
    )

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [{"url": "https://foo/bar.jar"}]

    run_osbs(descriptor, str(tmpdir), mocker)

    calls = [
        mocker.call(["git", "push", "-q", "origin", "branch"]),
        mocker.call(
            [
                "git",
                "commit",
                "-q",
                "-m",
                "Sync with path, commit 3b9283cb26b35511517ff5c0c3e11f490cba8feb",
            ]
        ),
        mocker.call(["git", "add", "--all", "Dockerfile"]),
        mocker.call(["git", "add", "--all", "fetch-artifacts-url.yaml"]),
    ]

    mock_check_call.assert_has_calls(calls, any_order=True)
    assert len(mock_check_call.mock_calls) == len(calls)
    assert "Skipping '.git' directory" in caplog.text
    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)
    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "md5": "098f6bcd4621d373cade4e832627b4f6",
        "target": "bar.jar",
        "url": "https://foo/bar.jar",
    }
    assert (
        "Artifact 'bar.jar' (as URL) added to fetch-artifacts-url.yaml" in caplog.text
    )


def test_osbs_builder_with_koji_target_based_on_branch(tmpdir, mocker, caplog):
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch.object(subprocess, "call")
    mocker.patch.object(subprocess, "check_call")

    tmpdir.mkdir("osbs").mkdir("repo").mkdir(".git").join("other").write_text(
        "Some content", "utf-8"
    )

    descriptor = image_descriptor.copy()

    run_osbs(descriptor, str(tmpdir), mocker)

    assert (
        "About to execute '/usr/bin/brew call --python buildContainer --kwargs {'src': 'git://somehost.com/containers/somerepo#3b9283cb26b35511517ff5c0c3e11f490cba8feb', 'target': 'branch-containers-candidate', 'opts': {'scratch': True, 'git_branch': 'branch', 'yum_repourls': []}}'."
        in caplog.text
    )


def test_osbs_builder_with_koji_target_in_descriptor(tmpdir, mocker, caplog):
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch.object(subprocess, "call")
    mocker.patch.object(subprocess, "check_call")

    tmpdir.mkdir("osbs").mkdir("repo").mkdir(".git").join("other").write_text(
        "Some content", "utf-8"
    )

    descriptor = image_descriptor.copy()

    descriptor["osbs"]["koji_target"] = "some-target"

    run_osbs(descriptor, str(tmpdir), mocker)

    assert (
        "About to execute '/usr/bin/brew call --python buildContainer --kwargs {'src': 'git://somehost.com/containers/somerepo#3b9283cb26b35511517ff5c0c3e11f490cba8feb', 'target': 'some-target', 'opts': {'scratch': True, 'git_branch': 'branch', 'yum_repourls': []}}'."
        in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_plain_file_creation(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with plain artifact.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch(
        "cekit.generator.osbs.get_brew_url", return_value="http://random.url/path"
    )
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    tmpdir.join("osbs").join("repo").join("fetch-artifacts-url.yaml").write_text(
        "Some content", "utf-8"
    )

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [{"name": "artifact_name", "md5": "123456"}]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "md5": "123456",
        "target": "artifact_name",
        "url": "http://random.url/path",
    }

    assert (
        "Artifact 'artifact_name' (as plain) added to fetch-artifacts-url.yaml"
        in caplog.text
    )


@pytest.mark.parametrize("flag", [[], ["--redhat"]])
def test_osbs_builder_with_fetch_artifacts_url_file_creation_1(
    tmpdir, mocker, caplog, flag
):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with URL artifact with md5 checksum.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {
            "name": "artifact_name",
            "md5": "123456",
            "url": "https://foo/bar.jar",
            "description": "http://foo.com/123456",
        }
    ]

    run_osbs(descriptor, str(tmpdir), mocker, general_command=flag)

    fau = os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml")
    with open(fau, "r") as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "md5": "123456",
        "target": "artifact_name",
        "url": "https://foo/bar.jar",
    }
    if len(flag):
        with open(fau) as myfile:
            assert "https://foo/bar.jar # http://foo.com/123456" in myfile.read()

    assert (
        "Artifact 'artifact_name' (as URL) added to fetch-artifacts-url.yaml"
        in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_url_file_creation_2(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with URL artifact with sha1 checksum.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {"name": "artifact_name", "sha1": "123456", "url": "https://foo/bar.jar"}
    ]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "sha1": "123456",
        "target": "artifact_name",
        "url": "https://foo/bar.jar",
    }

    assert (
        "Artifact 'artifact_name' (as URL) added to fetch-artifacts-url.yaml"
        in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_url_file_creation_3(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with URL artifact with sha256 checksum.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {"name": "artifact_name", "sha256": "123456", "url": "https://foo/bar.jar"}
    ]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "sha256": "123456",
        "target": "artifact_name",
        "url": "https://foo/bar.jar",
    }

    assert (
        "Artifact 'artifact_name' (as URL) added to fetch-artifacts-url.yaml"
        in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_url_file_creation_4(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with URL artifact with missing checksum.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    res = mocker.Mock()
    res.getcode.return_value = 200
    res.read.side_effect = [b"test", None]
    res.getheader.return_value = 0

    mocker.patch("cekit.tools.urlopen", return_value=res)
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [{"url": "https://foo/bar.jar"}]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "md5": "098f6bcd4621d373cade4e832627b4f6",
        "target": "bar.jar",
        "url": "https://foo/bar.jar",
    }

    assert (
        "Artifact 'bar.jar' (as URL) added to fetch-artifacts-url.yaml" in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_url_file_creation_5(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with URL artifact with sha256 checksum.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    res = mocker.Mock()
    res.getcode.return_value = 200
    res.read.side_effect = [b"test", None]
    res.getheader.return_value = 0

    mocker.patch("cekit.tools.urlopen", return_value=res)

    cfgcontents = """
[common]
fetch_artifact_domains = https://foo.domain, http://another.domain/path/name
ssl_verify = False
    """
    cfgfile = os.path.join(str(tmpdir), "config")
    with open(cfgfile, "w") as _file:
        _file.write(cfgcontents)

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {
            "name": "artifact_name",
            "sha256": "123456",
            "url": "https://foo.domain/bar.jar",
        },
        {
            "name": "another_artifact_name",
            "sha256": "654321",
            "url": "http://another.domain/path/name/bar.jar",
        },
        {
            "name": "not_allowed_in_fetch",
            "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            "url": "http://another.domain/wrong.jar",
        },
    ]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 2
    assert fetch_artifacts[0] == {
        "sha256": "123456",
        "target": "artifact_name",
        "url": "https://foo.domain/bar.jar",
    }
    assert fetch_artifacts[1] == {
        "sha256": "654321",
        "target": "another_artifact_name",
        "url": "http://another.domain/path/name/bar.jar",
    }

    assert (
        "Ignoring http://another.domain/wrong.jar as restricted to ['https://foo.domain', 'http://another.domain/path/name']"
        in caplog.text
    )
    assert (
        "Executing '['/usr/bin/rhpkg', 'new-sources', 'not_allowed_in_fetch']'"
        in caplog.text
    )
    assert (
        "Artifact 'artifact_name' (as URL) added to fetch-artifacts-url.yaml"
        in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_url_file_creation_multiple_hash(
    tmpdir, mocker, caplog
):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with URL artifact with multiple checksums.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    res = mocker.Mock()
    res.getcode.return_value = 200
    res.read.side_effect = [b"test", None]

    mocker.patch("cekit.tools.urlopen", return_value=res)
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {"sha256": "123456", "md5": "123456", "url": "https://foo/bar.jar"}
    ]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "sha256": "123456",
        "md5": "123456",
        "target": "bar.jar",
        "url": "https://foo/bar.jar",
    }

    assert (
        "Artifact 'bar.jar' (as URL) added to fetch-artifacts-url.yaml" in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_url_file_creation_naming(
    tmpdir, mocker, caplog
):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with name specified.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    res = mocker.Mock()
    res.getcode.return_value = 200
    res.read.side_effect = [b"test", None]

    mocker.patch("cekit.tools.urlopen", return_value=res)
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {"name": "myfile.jar", "sha256": "123456", "url": "https://foo/bar.jar"}
    ]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "sha256": "123456",
        "target": "myfile.jar",
        "url": "https://foo/bar.jar",
    }

    assert (
        "Artifact 'myfile.jar' (as URL) added to fetch-artifacts-url.yaml"
        in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_url_file_creation_naming_with_target(
    tmpdir, mocker, caplog
):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with name and target specified.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    res = mocker.Mock()
    res.getcode.return_value = 200
    res.read.side_effect = [b"test", None]

    mocker.patch("cekit.tools.urlopen", return_value=res)
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {
            "name": "an-additional-jar",
            "target": "myfile.jar",
            "sha256": "123456",
            "url": "https://foo/bar.jar",
        }
    ]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "sha256": "123456",
        "target": "myfile.jar",
        "url": "https://foo/bar.jar",
    }

    assert (
        "Artifact 'myfile.jar' (as URL) added to fetch-artifacts-url.yaml"
        in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_url_validate_dockerfile(
    tmpdir, mocker, caplog
):
    """
    Checks whether the fetch-artifacts-url.yaml and dockerfile are generated with correct paths.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    res = mocker.Mock()
    res.getcode.return_value = 200
    res.read.side_effect = [b"test", None]

    mocker.patch("cekit.tools.urlopen", return_value=res)
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {
            "name": "an-additional-jar",
            "target": "myfile.jar",
            "sha256": "123456",
            "url": "https://foo/bar.jar",
        }
    ]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-url.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert len(fetch_artifacts) == 1
    assert fetch_artifacts[0] == {
        "sha256": "123456",
        "target": "myfile.jar",
        "url": "https://foo/bar.jar",
    }

    with open(os.path.join(str(tmpdir), "target", "image", "Dockerfile"), "r") as _file:
        dockerfile = _file.read()

    assert "artifacts/myfile.jar" in dockerfile
    assert (
        "Artifact 'myfile.jar' (as URL) added to fetch-artifacts-url.yaml"
        in caplog.text
    )


def test_osbs_builder_with_fetch_artifacts_url_file_removal(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-url.yaml file is removed if exists
    and is not used anymore.

    https://github.com/cekit/cekit/issues/629
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch(
        "cekit.generator.osbs.get_brew_url", return_value="http://random.url/path"
    )
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    tmpdir.join("osbs").join("repo").join("fetch-artifacts-url.yaml").write_text(
        "Some content", "utf-8"
    )

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    run_osbs(image_descriptor, str(tmpdir), mocker)

    assert not os.path.exists(
        os.path.join(str(tmpdir), "osbs", "repo", "fetch-artifacts-url.yaml")
    )
    assert "Removing old 'fetch-artifacts-url.yaml' file" in caplog.text


def test_osbs_builder_with_fetch_artifacts_pnc_file_removal(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-pnc.yaml file is removed if exists
    and is not used anymore.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch(
        "cekit.generator.osbs.get_brew_url", return_value="http://random.url/path"
    )
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    tmpdir.join("osbs").join("repo").join("fetch-artifacts-pnc.yaml").write_text(
        "Some content", "utf-8"
    )

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-pnc.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    run_osbs(image_descriptor, str(tmpdir), mocker)

    assert not os.path.exists(
        os.path.join(str(tmpdir), "osbs", "repo", "fetch-artifacts-pnc.yaml")
    )
    assert "Removing old 'fetch-artifacts-pnc.yaml' file" in caplog.text


@pytest.mark.parametrize("flag", [[], ["--redhat"]])
def test_osbs_builder_container_yaml_existence(tmpdir, mocker, caplog, flag):
    """
    Make sure that the osbs section is properly merged.
    The evidence is that the container.yaml file is generated.

    https://github.com/cekit/cekit/issues/631
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch(
        "cekit.generator.osbs.get_brew_url", return_value="http://random.url/path"
    )
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["touch", "file"])
        subprocess.call(["git", "add", "file"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["osbs"]["configuration"] = {
        "container": {"compose": {"pulp_repos": True}}
    }

    run_osbs(descriptor, str(tmpdir), mocker, general_command=flag)

    assert os.path.exists(os.path.join(str(tmpdir), "osbs", "repo", "container.yaml"))
    dockerfile_path = os.path.join(str(tmpdir), "target", "image", "Dockerfile")
    assert os.path.exists(dockerfile_path) is True
    with open(dockerfile_path, "r") as _file:
        dockerfile = _file.read()

    assert "COPY $REMOTE_SOURCE $REMOTE_SOURCE_DIR" not in dockerfile


def test_osbs_builder_with_cachito_enabled(tmpdir, mocker, caplog):
    """
    Checks whether the generated Dockerfile has cachito instructions if container.yaml
    file has cachito section.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch(
        "cekit.generator.osbs.get_brew_url", return_value="http://random.url/path"
    )
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["touch", "file"])
        subprocess.call(["git", "add", "file"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["osbs"]["configuration"] = {
        "container": {
            "remote_source": {"ref": "123456", "repo": "http://foo.bar.com"},
            "compose": {"pulp_repos": True},
        }
    }

    run_osbs(descriptor, str(tmpdir), mocker)

    dockerfile_path = os.path.join(str(tmpdir), "target", "image", "Dockerfile")
    assert os.path.exists(dockerfile_path) is True
    with open(dockerfile_path, "r") as _file:
        dockerfile = _file.read()
        assert (
            """## START target image test/image:1.0
## \\
    FROM centos:7


    USER root

    COPY $REMOTE_SOURCE $REMOTE_SOURCE_DIR
    WORKDIR $REMOTE_SOURCE_DIR/app

###### START image 'test/image:1.0'
###### \\
        # Set 'test/image' image defined environment variables
        ENV \\
            JBOSS_IMAGE_NAME="test/image" \\
            JBOSS_IMAGE_VERSION="1.0"
        # Set 'test/image' image defined labels
        LABEL \\
            foo="bar" \\
            io.cekit.version="VVVVV" \\
            labela="a" \\
            name="test/image" \\
            version="1.0"
###### /
###### END image 'test/image:1.0'

    RUN rm -rf $REMOTE_SOURCE_DIR
""".replace(
                "VVVVV", __version__
            )
            in dockerfile
        )
    assert re.search("Cachito definition is .*http://foo.bar.com", caplog.text)


def test_osbs_builder_with_rhpam_1(tmpdir, caplog):
    """
    Verify that multi-stage build has Cachito instructions enabled.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    shutil.copytree(
        os.path.join(os.path.dirname(__file__), "images", "rhpam1"),
        os.path.join(str(tmpdir), "rhpam"),
    )

    cfgcontents = """
[common]
redhat = True
    """
    cfgfile = os.path.join(str(tmpdir), "config.cfg")
    with open(cfgfile, "w") as _file:
        _file.write(cfgcontents)

    run_cekit(
        (os.path.join(str(tmpdir), "rhpam")),
        parameters=[
            "--config",
            cfgfile,
            "-v",
            "--work-dir",
            str(tmpdir),
            "build",
            "--dry-run",
            "osbs",
        ],
    )

    dockerfile_path = os.path.join(
        str(tmpdir), "rhpam", "target", "image", "Dockerfile"
    )
    assert os.path.exists(dockerfile_path) is True
    with open(dockerfile_path, "r") as _file:
        dockerfile = _file.read()
        print("\n" + dockerfile + "\n")
        assert (
            """# This is a Dockerfile for the rhpam-7/rhpam-kogito-operator:7.11 image.

## START builder image operator-builder:7.11
## \\
    FROM registry.access.redhat.com/ubi8/go-toolset:1.14.12 AS operator-builder
    USER root

    COPY $REMOTE_SOURCE $REMOTE_SOURCE_DIR
    WORKDIR $REMOTE_SOURCE_DIR/app

###### START module 'org.kie.kogito.builder:7.11'
###### \\
        # Copy 'org.kie.kogito.builder' module general artifacts to '/workspace/' destination
        COPY \\
            main.go \\
            /workspace/
        # Copy 'org.kie.kogito.builder' module content
        COPY modules/org.kie.kogito.builder /tmp/scripts/org.kie.kogito.builder
        # Custom scripts from 'org.kie.kogito.builder' module
        USER root
        RUN [ "sh", "-x", "/tmp/scripts/org.kie.kogito.builder/install.sh" ]
###### /
###### END module 'org.kie.kogito.builder:7.11'

###### START image 'operator-builder:7.11'
###### \\
        # Set 'operator-builder' image defined environment variables
        ENV \\
            JBOSS_IMAGE_NAME="rhpam-7/rhpam-kogito-operator" \\
            JBOSS_IMAGE_VERSION="7.11"
        # Set 'operator-builder' image defined labels
        LABEL \\
            name="rhpam-7/rhpam-kogito-operator" \\
            version="7.11"
###### /
###### END image 'operator-builder:7.11'

    RUN rm -rf $REMOTE_SOURCE_DIR

## /
## END builder image

## START target image rhpam-7/rhpam-kogito-operator:7.11
## \\
    FROM registry.access.redhat.com/ubi8/ubi-minimal:latest


    USER root

###### START image 'rhpam-7/rhpam-kogito-operator:7.11'
###### \\
        # Copy 'rhpam-7/rhpam-kogito-operator' image stage artifacts
        COPY --from=operator-builder /workspace/rhpam-kogito-operator-manager /usr/local/bin/rhpam-kogito-operator-manager
        # Set 'rhpam-7/rhpam-kogito-operator' image defined environment variables
        ENV \\
            JBOSS_IMAGE_NAME="rhpam-7/rhpam-kogito-operator" \\
            JBOSS_IMAGE_VERSION="7.11"
        # Set 'rhpam-7/rhpam-kogito-operator' image defined labels
        LABEL \\
            com.redhat.component="rhpam-7-kogito-rhel8-operator-container" \\
            description="Runtime Image for the RHPAM Kogito Operator" \\
            io.cekit.version="VVVVV" \\
            io.k8s.description="Operator for deploying RHPAM Kogito Application" \\
            io.k8s.display-name="Red Hat PAM Kogito Operator" \\
            io.openshift.tags="rhpam,kogito,operator" \\
            maintainer="bsig-cloud@redhat.com" \\
            name="rhpam-7/rhpam-kogito-operator" \\
            summary="Runtime Image for the RHPAM Kogito Operator" \\
            version="7.11"
###### /
###### END image 'rhpam-7/rhpam-kogito-operator:7.11'



    # Switch to 'root' user and remove artifacts and modules
    USER root
    RUN [ ! -d /tmp/scripts ] || rm -rf /tmp/scripts
    RUN [ ! -d /tmp/artifacts ] || rm -rf /tmp/artifacts
    # Define the user
    USER 1001
## /
## END target image""".replace(
                "VVVVV", __version__
            )
            in dockerfile
        )
    container_path = os.path.join(
        str(tmpdir), "rhpam", "target", "image", "container.yaml"
    )
    assert os.path.exists(container_path) is True
    with open(container_path, "r") as _file:
        containerfile = _file.read()
        print("\n" + containerfile + "\n")
        assert (
            """image_build_method: imagebuilder
platforms:
  only:
  - x86_64
remote_source:
  pkg_managers:
  - gomod
  ref: db4a5d18f5f52a64083d8f1bd1776ad60a46904c
  repo: https://github.com/kiegroup/rhpam-kogito-operator"""
            in containerfile
        )


def test_osbs_builder_with_rhpam_2(tmpdir, caplog):
    """
    Verify that multi-stage build with dual configuration/container (for Cachito) will fail.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    shutil.copytree(
        os.path.join(os.path.dirname(__file__), "images", "rhpam2"),
        os.path.join(str(tmpdir), "rhpam"),
    )

    cfgcontents = """
[common]
redhat = True
    """
    cfgfile = os.path.join(str(tmpdir), "config.cfg")
    with open(cfgfile, "w") as _file:
        _file.write(cfgcontents)

    with Chdir(os.path.join(str(tmpdir), "rhpam")):
        result = CliRunner().invoke(
            cli,
            [
                "--config",
                cfgfile,
                "-v",
                "--work-dir",
                str(tmpdir),
                "build",
                "--dry-run",
                "osbs",
            ],
            catch_exceptions=True,
        )
        sys.stdout.write("\n")
        sys.stdout.write(result.output)

        assert result.exit_code == 1

    assert "Found multiple container definitions " in caplog.text


def test_osbs_builder_with_rhpam_3(tmpdir, caplog):
    """
    Verify that multi-stage build has Cachito instructions enabled.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    shutil.copytree(
        os.path.join(os.path.dirname(__file__), "images", "rhpam3"),
        os.path.join(str(tmpdir), "rhpam"),
    )

    cfgcontents = """
[common]
redhat = True
    """
    cfgfile = os.path.join(str(tmpdir), "config.cfg")
    with open(cfgfile, "w") as _file:
        _file.write(cfgcontents)

    run_cekit(
        (os.path.join(str(tmpdir), "rhpam")),
        parameters=[
            "--config",
            cfgfile,
            "-v",
            "--work-dir",
            str(tmpdir),
            "build",
            "--dry-run",
            "osbs",
        ],
    )

    dockerfile_path = os.path.join(
        str(tmpdir), "rhpam", "target", "image", "Dockerfile"
    )
    assert os.path.exists(dockerfile_path) is True
    with open(dockerfile_path, "r") as _file:
        dockerfile = _file.read()
        print("\n" + dockerfile + "\n")
        assert (
            """# This is a Dockerfile for the rhpam-7/rhpam-kogito-operator:7.11 image.

## START builder image operator-builder-one:7.11
## \\
    FROM registry.access.redhat.com/ubi8/go-toolset:1.14.12 AS operator-builder-one
    USER root

###### START module 'org.kie.kogito.builder:7.11'
###### \\
        # Copy 'org.kie.kogito.builder' module general artifacts to '/workspace/' destination
        COPY \\
            main.go \\
            /workspace/
        # Copy 'org.kie.kogito.builder' module content
        COPY modules/org.kie.kogito.builder /tmp/scripts/org.kie.kogito.builder
        # Custom scripts from 'org.kie.kogito.builder' module
        USER root
        RUN [ "sh", "-x", "/tmp/scripts/org.kie.kogito.builder/install.sh" ]
###### /
###### END module 'org.kie.kogito.builder:7.11'

###### START image 'operator-builder-one:7.11'
###### \\
        # Set 'operator-builder-one' image defined environment variables
        ENV \\
            JBOSS_IMAGE_NAME="rhpam-7/rhpam-kogito-operator" \\
            JBOSS_IMAGE_VERSION="7.11"
        # Set 'operator-builder-one' image defined labels
        LABEL \\
            name="rhpam-7/rhpam-kogito-operator" \\
            version="7.11"
###### /
###### END image 'operator-builder-one:7.11'


## /
## END builder image
## START builder image operator-builder-two:7.11
## \\
    FROM registry.access.redhat.com/ubi8/go-toolset:1.14.12 AS operator-builder-two
    USER root

    COPY $REMOTE_SOURCE $REMOTE_SOURCE_DIR
    WORKDIR $REMOTE_SOURCE_DIR/app

###### START image 'operator-builder-two:7.11'
###### \\
        # Set 'operator-builder-two' image defined environment variables
        ENV \\
            JBOSS_IMAGE_NAME="rhpam-7/rhpam-kogito-operator" \\
            JBOSS_IMAGE_VERSION="7.11"
        # Set 'operator-builder-two' image defined labels
        LABEL \\
            name="rhpam-7/rhpam-kogito-operator" \\
            version="7.11"
###### /
###### END image 'operator-builder-two:7.11'

    RUN rm -rf $REMOTE_SOURCE_DIR

## /
## END builder image

## START target image rhpam-7/rhpam-kogito-operator:7.11
## \\
    FROM registry.access.redhat.com/ubi8/ubi-minimal:latest


    USER root

###### START image 'rhpam-7/rhpam-kogito-operator:7.11'
###### \\
        # Copy 'rhpam-7/rhpam-kogito-operator' image stage artifacts
        COPY --from=operator-builder /workspace/rhpam-kogito-operator-manager /usr/local/bin/rhpam-kogito-operator-manager
        # Set 'rhpam-7/rhpam-kogito-operator' image defined environment variables
        ENV \\
            JBOSS_IMAGE_NAME="rhpam-7/rhpam-kogito-operator" \\
            JBOSS_IMAGE_VERSION="7.11"
        # Set 'rhpam-7/rhpam-kogito-operator' image defined labels
        LABEL \\
            com.redhat.component="rhpam-7-kogito-rhel8-operator-container" \\
            description="Runtime Image for the RHPAM Kogito Operator" \\
            io.cekit.version="VVVVV" \\
            io.k8s.description="Operator for deploying RHPAM Kogito Application" \\
            io.k8s.display-name="Red Hat PAM Kogito Operator" \\
            io.openshift.tags="rhpam,kogito,operator" \\
            maintainer="bsig-cloud@redhat.com" \\
            name="rhpam-7/rhpam-kogito-operator" \\
            summary="Runtime Image for the RHPAM Kogito Operator" \\
            version="7.11"
###### /
###### END image 'rhpam-7/rhpam-kogito-operator:7.11'



    # Switch to 'root' user and remove artifacts and modules
    USER root
    RUN [ ! -d /tmp/scripts ] || rm -rf /tmp/scripts
    RUN [ ! -d /tmp/artifacts ] || rm -rf /tmp/artifacts
    # Define the user
    USER 1001
## /
## END target image""".replace(
                "VVVVV", __version__
            )
            in dockerfile
        )
    container_path = os.path.join(
        str(tmpdir), "rhpam", "target", "image", "container.yaml"
    )
    assert os.path.exists(container_path) is True
    with open(container_path, "r") as _file:
        containerfile = _file.read()
        print("\n" + containerfile + "\n")
        assert (
            """image_build_method: imagebuilder
platforms:
  only:
  - x86_64
remote_source:
  pkg_managers:
  - gomod
  ref: db4a5d18f5f52a64083d8f1bd1776ad60a46904c
  repo: https://github.com/kiegroup/rhpam-kogito-operator"""
            in containerfile
        )


def test_osbs_builder_with_fetch_artifacts_pnc_file_creation_1(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-pnc.yaml file is generated with correct artifact ids.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-pnc.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {"pnc_build_id": "123456", "pnc_artifact_id": "54321", "name": "foo.jar"}
    ]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-pnc.yaml"), "r"
    ) as _file:
        fetch_artifacts = yaml.safe_load(_file)

    assert "Executing '['/usr/bin/rhpkg', 'new-sources', 'foo.jar']'" not in caplog.text
    assert fetch_artifacts["builds"] == [
        {"build_id": "123456", "artifacts": [{"id": "54321", "target": "foo.jar"}]}
    ]


def test_osbs_builder_with_fetch_artifacts_pnc_file_creation_2(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-pnc.yaml file is generated with correct artifact ids.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")

    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    tmpdir.mkdir("osbs").mkdir("repo")

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-pnc.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [
        {"target": "boo.jar", "pnc_build_id": "123456", "pnc_artifact_id": "00001"},
        {
            "target": "foo.jar",
            "pnc_build_id": "123456",
            "pnc_artifact_id": "54321",
            "url": "http://www.dummy.com/build/artifact.jar",
        },
        {
            "target": "foobar/goo.zip",
            "pnc_build_id": "987654",
            "pnc_artifact_id": "00002",
        },
    ]

    run_osbs(descriptor, str(tmpdir), mocker)

    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-pnc.yaml"), "r"
    ) as _file:
        fetch_artifacts = _file.read()
    with open(
        os.path.join(str(tmpdir), "target", "image", "fetch-artifacts-pnc.yaml"), "r"
    ) as _file:
        fetch_artifacts_yaml = yaml.safe_load(_file)
    print("Read fetch_pnc_artifacts {}\n".format(fetch_artifacts))
    assert "# Created by CEKit version" in fetch_artifacts
    assert fetch_artifacts_yaml["builds"] == [
        {
            "build_id": "123456",
            "artifacts": [
                {"id": "00001", "target": "boo.jar"},
                {"id": "54321", "target": "foo.jar"},
            ],
        },
        {
            "build_id": "987654",
            "artifacts": [{"id": "00002", "target": "foobar/goo.zip"}],
        },
    ]
    assert (
        """builds:
- build_id: '123456'
  artifacts:
  - id: '00001'
    target: boo.jar
  - id: '54321' # http://www.dummy.com/build/artifact.jar
    target: foo.jar
- build_id: '987654'
  artifacts:
  - id: '00002'
    target: foobar/goo.zip"""
        in fetch_artifacts
    )
    with open(os.path.join(str(tmpdir), "target", "image", "Dockerfile"), "r") as _file:
        dockerfile = _file.read()
    assert (
        """COPY \\
            artifacts/boo.jar \\
            artifacts/foo.jar \\
            artifacts/foobar/goo.zip \\
            /tmp/artifacts/"""
        in dockerfile
    )


def test_osbs_builder_with_brew_and_lookaside(tmpdir, mocker, caplog):
    """
    Checks whether the fetch-artifacts-url.yaml file is generated with plain artifact.
    """

    caplog.set_level(logging.DEBUG, logger="cekit")
    mocker.patch("cekit.crypto.get_sum", return_value="123456")
    mocker.patch("cekit.cache.artifact.get_sum", return_value="123456")
    mocker.patch("cekit.tools.decision", return_value=True)
    mocker.patch("cekit.tools.urlopen")
    mocker.patch(
        "cekit.generator.osbs.get_brew_url", return_value="http://random.url/path"
    )
    mocker.patch.object(subprocess, "check_output")
    mocker.patch("cekit.builders.osbs.DistGit.push")

    work_dir = str(tmpdir.mkdir("work_dir"))
    image_dir = str(tmpdir)
    os.makedirs(work_dir + "/cache")
    with open(os.path.join(image_dir, "config"), "w") as fd:
        fd.write("[common]\n")
        fd.write("cache_url = #filename#\n")
    with open(os.path.join(image_dir, "artifact_name"), "w") as fd:
        fd.write("jar-content")

    tmpdir.mkdir("osbs").mkdir("repo")

    tmpdir.join("osbs").join("repo").join("fetch-artifacts-url.yaml").write_text(
        "Some content", "utf-8"
    )

    with Chdir(os.path.join(str(tmpdir), "osbs", "repo")):
        subprocess.call(["git", "init"])
        subprocess.call(["git", "add", "fetch-artifacts-url.yaml"])
        subprocess.call(["git", "commit", "-m", "Dummy"])

    descriptor = image_descriptor.copy()

    descriptor["artifacts"] = [{"name": "artifact_name", "sha256": "123456"}]

    run_osbs(descriptor, str(tmpdir), mocker)

    assert (
        "Unable to use Brew as artifact does not have md5 checksum defined"
        in caplog.text
    )
    assert (
        "Plain artifact artifact_name could not be found in Brew, trying to handle it using lookaside cache"
        in caplog.text
    )
