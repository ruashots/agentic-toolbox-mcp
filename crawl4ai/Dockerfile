# tb-crawl4ai: the frozen Crawl4AI image with its record-keeping switched off and
# its browser's protections back on (patch-crawl4ai.py).
#
# Privacy rule for the whole toolbox: keep no record of what agents asked for or
# got back. Stock Crawl4AI keeps three:
#   1. every fetched page, written to a cache database and content files under
#      ~/.crawl4ai. The /md endpoint the gateway uses runs in WRITE_ONLY mode: it
#      writes every page and never reads it back. -> BYPASS (no read, no write).
#   2. crawler.log / crawler_db.log files under ~/.crawl4ai. -> no log file.
#   3. Redis snapshots (dump.rdb) in /app. -> snapshots off.
# Each edit is checked; the build fails if the upstream text is not found.
FROM unclecode/crawl4ai@sha256:a45fd08f8f15f67026c1bff0a151f0479244caf6751a0c6943b3870efafcd025

USER root
RUN set -eu; \
    P=/usr/local/lib/python3.12/site-packages/crawl4ai; \
    grep -q 'cache_mode = CacheMode.ENABLED if cache == "1" else CacheMode.WRITE_ONLY' /app/api.py; \
    sed -i 's/cache_mode = CacheMode.ENABLED if cache == "1" else CacheMode.WRITE_ONLY/cache_mode = CacheMode.BYPASS  # toolbox: keep no copy of fetched pages/' /app/api.py; \
    grep -q 'CacheMode.BYPASS  # toolbox' /app/api.py; \
    grep -q 'log_file=os.path.join(base_directory, ".crawl4ai", "crawler.log"),' $P/async_webcrawler.py; \
    sed -i 's|log_file=os.path.join(base_directory, ".crawl4ai", "crawler.log"),|log_file=None,  # toolbox: no log file|' $P/async_webcrawler.py; \
    grep -q 'log_file=os.path.join(base_directory, ".crawl4ai", "crawler_db.log"),' $P/async_database.py; \
    sed -i 's|log_file=os.path.join(base_directory, ".crawl4ai", "crawler_db.log"),|log_file=None,  # toolbox: no log file|' $P/async_database.py; \
    grep -q 'command=/usr/bin/redis-server --loglevel notice' /app/supervisord.conf; \
    sed -i 's|command=/usr/bin/redis-server --loglevel notice|command=/usr/bin/redis-server --loglevel warning --save "" --appendonly no|' /app/supervisord.conf; \
    grep -q -- '--save ""' /app/supervisord.conf; \
    find $P -name '*.pyc' -path '*crawl4ai*' -delete
COPY patch-crawl4ai.py /tmp/patch-crawl4ai.py
RUN python /tmp/patch-crawl4ai.py && rm -f /tmp/patch-crawl4ai.py && rm -rf /app/__pycache__
USER appuser
