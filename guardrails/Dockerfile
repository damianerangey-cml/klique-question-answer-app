FROM python:3.11
WORKDIR /app
COPY requirements.txt .
# annoy (a nemoguardrails dependency) compiles from source and by default tunes itself to the build machine's CPU
# (-march=native), which crashes on older CPUs with an illegal instruction. Build it for any x86-64 CPU instead.
RUN ANNOY_COMPILER_ARGS="-D_CRT_SECURE_NO_WARNINGS,-DANNOYLIB_MULTITHREADED_BUILD,-O3,-ffast-math,-fno-associative-math,-march=x86-64" \
    pip install --no-cache-dir -r requirements.txt
COPY config.tmpl.yml start.sh ./
EXPOSE 8000
CMD ["sh", "/app/start.sh"]
