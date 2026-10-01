import base64
import json

import pytest


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


@pytest.fixture
def make_jwt():
    def make(exp: float) -> str:
        return f"{_b64({'alg': 'HS256'})}.{_b64({'exp': exp, 'uid': 'test'})}.c2ln"
    return make
