FROM    python:3-alpine
LABEL   maintainer="Thijs Eilander <eilander@myguard.nl>"

COPY    bootstrap.sh /bootstrap.sh

RUN set -x; \
        \
	pip install --upgrade pip ;\
        apk add --no-cache --virtual .build-deps \
                git \
        ;\
	\
	cd / ;\
	/usr/bin/git clone https://github.com/Reloisback/Whiteout-Survival-Discord-Bot.git app ;\
	cd /app ;\
	echo 0 > bot_token.txt ;\
	sh -c 'echo y' | python main.py 2>/dev/null ;\
	rm bot_token.txt ;\
	apk del .build-deps ;\
	chmod +x /bootstrap.sh

WORKDIR /app
ENTRYPOINT ["/bootstrap.sh"]
