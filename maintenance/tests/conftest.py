"""Temporary native working-state storage for migrated consumer tests."""

import pytest

from maintenance.tests.working_state_support import native_dialogue


@pytest.fixture
def make_dialogue(request):
    def make(path, owners=None, bridge=None):
        return native_dialogue(path, request.addfinalizer, owners, bridge)

    return make
