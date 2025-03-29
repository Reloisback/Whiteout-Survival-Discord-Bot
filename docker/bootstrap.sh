#!/bin/sh

echo "[WSDB] For more information please see https://github.com/Reloisback/Whiteout-Survival-Discord-Bot"

cd /app


if [ ! -n "${DISCORD_BOT_TOKEN}" ]; then
	echo "please set DISCORD_BOT_TOKEN"
	exit
fi

if [ -n "${DISCORD_BOT_TOKEN}" ]; then
    echo "${DISCORD_BOT_TOKEN}" > bot_token.txt
fi

sh -c 'echo y' | python main.py
