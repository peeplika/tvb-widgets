# -*- coding: utf-8 -*-
#
# "TheVirtualBrain - Widgets" package
#
# (c) 2022-2023, TVB Widgets Team
#

import os
from .logger.builder import get_logger

LOGGER = get_logger(__name__)


def get_current_token():
    try:
        bearer_token = clb_oauth.get_token()
        return bearer_token

    except Exception:
        LOGGER.info("We could not find Collab Auth Token, we will search for env CLB_AUTH variable")

        env_token = os.environ.get("CLB_AUTH")
        if env_token is not None:
            LOGGER.info("We found Collab Auth in environment!")
            return env_token

        raise RuntimeError("Could not authenticate in Collab. Try to define local env  CLB_AUTH or login EBRAINS")
