  wos-discord-bot:
    image: eilandert/whiteout-survival-discord-bot:v4
    container_name: wos-discord-bot
    volumes:
      - /opt/docker/wos/db/:/app/db
    restart: unless-stopped
    environment:
      - DISCORD_BOT_TOKEN=<your bot token here>
