# -*- encoding: utf-8 -*-

import os
import shutil
import sys
import tempfile

import pytest
import yaml
from click.testing import CliRunner

from cekit.cli import cli
from cekit.tools import Chdir

image_descriptors = [
    {
        "schema_version": 1,
        "from": "alpine:3.9",
        "name": "test/image",
        "version": "1.0",
        "labels": [{"name": "foo", "value": "bar"}, {"name": "labela", "value": "a"}],
        "envs": [{"name": "baz", "value": "qux"}, {"name": "enva", "value": "a"}],
        "run": {"cmd": ["tail", "-f", "/dev/null"]},
    },
    {
        "schema_version": 1,
        "from": "alpine:3.9",
        "name": "image",
        "version": "1.0-slim",
        "labels": [{"name": "foo", "value": "bar"}, {"name": "labela", "value": "a"}],
        "envs": [{"name": "baz", "value": "qux"}, {"name": "enva", "value": "a"}],
        "run": {"cmd": ["tail", "-f", "/dev/null"]},
    },
    {
        "from": "alpine:3.9",
        "name": "image",
        "version": "slimmed",
        "labels": [{"name": "foo", "value": "bar"}, {"name": "labela", "value": "a"}],
        "envs": [{"name": "baz", "value": "qux"}, {"name": "enva", "value": "a"}],
        "run": {"cmd": ["tail", "-f", "/dev/null"]},
    },
]


@pytest.fixture(scope="function", name="build_image", params=image_descriptors)
def fixture_build_image(request):
    def _build_image(overrides=None):
        image_descriptor = request.param
        image_dir = tempfile.mkdtemp(prefix="tmp-cekit-test")

        with open(os.path.join(image_dir, "image.yaml"), "w") as fd:
            yaml.dump(image_descriptor, fd, default_flow_style=False)

        args = ["-v", "build"]

        if overrides:
            args += ["--overrides", overrides]

        args.append("docker")

        with Chdir(image_dir):
            result = CliRunner().invoke(cli, args, catch_exceptions=False)

        sys.stdout.write("\n")
        sys.stdout.write(result.output)

        assert result.exit_code == 0

        return image_dir

    return _build_image


@pytest.mark.skipif(
    os.path.exists("/var/run/docker.sock") is False, reason="No Docker available"
)
def test_execute_simple_behave_test(build_image):
    feature = """@test @image
Feature: Basic tests

  Scenario: Check that the labels are correctly set
    Given image is built
    Then the image should contain label foo with value bar
     And the image should contain label labela with value a
    """

    test_image_dir = build_image()

    features_dir = os.path.join(test_image_dir, "tests", "features")

    os.makedirs(features_dir)

    with open(os.path.join(features_dir, "basic.feature"), "w") as fd:
        fd.write(feature)

    with Chdir(test_image_dir):
        result = CliRunner().invoke(
            cli, ["-v", "test", "behave"], catch_exceptions=False
        )

    sys.stdout.write("\n")
    sys.stdout.write(result.output)

    assert result.exit_code == 0
    assert "1 feature passed, 0 failed, 0 skipped" in result.output
    assert "1 scenario passed, 0 failed, 0 skipped" in result.output
    assert "3 steps passed, 0 failed, 0 skipped, 0 undefined" in result.output

    shutil.rmtree(os.path.join(test_image_dir, "target"), ignore_errors=True)


@pytest.mark.skipif(
    os.path.exists("/var/run/docker.sock") is False, reason="No Docker available"
)
def test_execute_simple_behave_test_with_overrides(build_image):
    feature = """@different
Feature: Basic tests

  Scenario: Check that the labels are correctly set
    Given image is built
    Then the image should contain label foo with value bar
     And the image should contain label labela with value a
    """

    overrides = '{"name": "different/image"}'

    test_image_dir = build_image(overrides)

    features_dir = os.path.join(test_image_dir, "tests", "features")

    os.makedirs(features_dir)

    with open(os.path.join(features_dir, "basic.feature"), "w") as fd:
        fd.write(feature)

    with Chdir(test_image_dir):
        result = CliRunner().invoke(
            cli,
            ["-v", "test", "--overrides", overrides, "behave"],
            catch_exceptions=False,
        )

    sys.stdout.write("\n")
    sys.stdout.write(result.output)

    assert result.exit_code == 0
    assert "1 feature passed, 0 failed, 0 skipped" in result.output
    assert "1 scenario passed, 0 failed, 0 skipped" in result.output
    assert "3 steps passed, 0 failed, 0 skipped, 0 undefined" in result.output

    shutil.rmtree(os.path.join(test_image_dir, "target"), ignore_errors=True)


@pytest.mark.skipif(
    os.path.exists("/var/run/docker.sock") is False, reason="No Docker available"
)
def test_execute_behave_test_from_module():

    # given: (image is built)
    image_dir = os.path.join(tempfile.mkdtemp(prefix="tmp-cekit-test"), "project")

    shutil.copytree(
        os.path.join(os.path.dirname(__file__), "images", "module-tests"), image_dir
    )

    with Chdir(image_dir):
        build_result = CliRunner().invoke(
            cli, ["-v", "build", "docker"], catch_exceptions=False
        )

    sys.stdout.write("\n")
    sys.stdout.write(build_result.output)

    assert build_result.exit_code == 0

    # when: tests are run
    with Chdir(image_dir):
        test_result = CliRunner().invoke(
            cli, ["-v", "test", "behave"], catch_exceptions=False
        )

    sys.stdout.write("\n")
    sys.stdout.write(test_result.output)

    # then:
    assert test_result.exit_code == 0
    assert "1 feature passed, 0 failed, 0 skipped" in test_result.output
    assert "1 scenario passed, 0 failed, 0 skipped" in test_result.output
    assert "2 steps passed, 0 failed, 0 skipped, 0 undefined" in test_result.output


@pytest.mark.skipif(
    os.path.exists("/var/run/docker.sock") is False, reason="No Docker available"
)
def test_simple_image_test(build_image):
    image_dir = build_image()

    shutil.copytree(
        os.path.join(os.path.dirname(__file__), "modules"),
        os.path.join(image_dir, "tests", "modules"),
    )

    feature_files = os.path.join(image_dir, "tests", "features", "test.feature")

    os.makedirs(os.path.dirname(feature_files))

    feature_label_test = """
    @test @image
    Feature: Test test

      Scenario: Check label foo
        When container is started as uid 0 with process sleep
        then the image should contain label foo with value bar
    """

    with open(feature_files, "w") as fd:
        fd.write(feature_label_test)

    with Chdir(image_dir):
        test_result = CliRunner().invoke(
            cli,
            ["-v", "test", "--image", "test/image:1.0", "behave"],
            catch_exceptions=False,
        )

    sys.stdout.write("\n")
    sys.stdout.write(test_result.output)

    # then:
    assert test_result.exit_code == 0
    assert "1 feature passed, 0 failed, 0 skipped" in test_result.output
    assert "1 scenario passed, 0 failed, 0 skipped" in test_result.output
    assert "2 steps passed, 0 failed, 0 skipped, 0 undefined" in test_result.output
    assert os.path.exists(os.path.join(image_dir, "target", "image", "Dockerfile"))
