# -*- encoding: utf-8 -*-

from __future__ import print_function

import os
import platform
import shutil

import pytest
from click.testing import CliRunner

from cekit.cli import cli
from cekit.tools import Chdir


def run_cekit(image_dir, args=None, env=None):
    if args is None:
        args = ["build", "podman"]

    if env is None:
        env = {}

    with Chdir(image_dir):
        result = CliRunner(env=env).invoke(cli, args, catch_exceptions=False)
        assert result.exit_code == 0
        return result


@pytest.mark.skipif(
    platform.system() == "Darwin", reason="Disabled on macOS, cannot run Podman"
)
def test_podman_builder_with_alpine_image(tmpdir):
    tmpdir = str(tmpdir)

    shutil.copytree(
        os.path.join(os.path.dirname(__file__), "images", "alpine"),
        os.path.join(tmpdir, "alpine"),
    )

    # The 'BUILDAH_LAYERS' environment variable is required to not cache intermediate layers
    # See: https://bugzilla.redhat.com/show_bug.cgi?id=1746022
    run_cekit(os.path.join(tmpdir, "alpine"), env={"BUILDAH_LAYERS": "false"})


@pytest.mark.skipif(
    platform.system() == "Darwin", reason="Disabled on macOS, cannot run Podman"
)
def test_podman_from_scratch(tmpdir):
    tmpdir = str(tmpdir)

    shutil.copytree(
        os.path.join(os.path.dirname(__file__), "images", "scratch"),
        os.path.join(tmpdir, "scratch"),
    )

    # The 'BUILDAH_LAYERS' environment variable is required to not cache intermediate layers
    # See: https://bugzilla.redhat.com/show_bug.cgi?id=1746022
    run_cekit(os.path.join(tmpdir, "scratch"), env={"BUILDAH_LAYERS": "false"})


@pytest.mark.skipif(
    platform.system() == "Darwin", reason="Disabled on macOS, cannot run Podman"
)
def test_podman_operator_metadata(tmpdir):
    tmpdir = str(tmpdir)

    shutil.copytree(
        os.path.join(os.path.dirname(__file__), "images", "operator-metadata"),
        os.path.join(tmpdir, "operator-metadata"),
    )

    # The 'BUILDAH_LAYERS' environment variable is required to not cache intermediate layers
    # See: https://bugzilla.redhat.com/show_bug.cgi?id=1746022
    run_cekit(
        os.path.join(tmpdir, "operator-metadata"),
        args=["--redhat", "build", "podman"],
        env={"BUILDAH_LAYERS": "false"},
    )
