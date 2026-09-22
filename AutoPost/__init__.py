# (c) @TheLx0980
# Year : 2023

import logging, re
from os import getenv
from typing import List  

id_pattern = re.compile(r'^-?\d+$')

API_ID = getenv('API_ID', '2990')
API_HASH = getenv('API_HASH', '235b12e862082052822fd')
BOT_TOKEN = getenv('BOT_TOKEN', '')

BOT_OWNER_IDS: List[int] = [
    int(admin) if id_pattern.search(admin) else admin 
    for admin in getenv('ADMINS', '').split()
]


DB_URL = getenv('DB_URL', '')
DAILY_FORWARD_LIMIT: int = int(getenv("DAILY_FORWARD_LIMIT", 2000))

def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)
