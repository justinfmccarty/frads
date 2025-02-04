"""Check for Radiance installation
Does not raise exception when Radiance not installed properly, hence the print statement instaed of raise.
also modify file limit on unix systems."""

import logging
import shutil
import subprocess as sp
import sys

__version__ = "0.2.7"

logger = logging.getLogger("frads")

# Check if Radiance is installed more or less
rad_progs = [
    "rfluxmtx",
    "total",
    "getinfo",
    "pcomb",
    "dctimestep",
    "rmtxop",
    "gendaymtx",
    "rtrace",
]

for prog in rad_progs:
    ppath = shutil.which(prog)
    if ppath is None:
        logger.info(f"{prog} not found; check Radiance installation")

try:
    # Check Radiance version, need to be at least 5.X
    version_check = sp.run(
        ["rtrace", "-version"], check=True, stdout=sp.PIPE
    ).stdout.decode()
    msg = "Please upgrade to Radiance version 5.3 or later"
    try:
        rad_version = float(version_check.split()[1][:3])
        if rad_version < 5.3:
            logger.info(msg)
    except ValueError:
        logger.info(msg)
except FileNotFoundError as err:
    logger.info(err)
