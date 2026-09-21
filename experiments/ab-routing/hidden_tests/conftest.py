"""Shared across all hidden_tests/task* suites: the --target option (the
throwaway clone of the solution under test that score.py hands each suite;
never a task copy itself). pytest.ini in this directory anchors rootdir here
so this file, and each taskN/conftest.py, is always discovered regardless of
the invocation's own cwd."""


def pytest_addoption(parser):
    parser.addoption("--target", action="store", required=True,
                      help="path to a throwaway clone of the solution under test")
