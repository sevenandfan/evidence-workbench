# Minimal offline demo image. Real public datasets are acquired at runtime and
# are intentionally not copied into the image.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt && useradd -u 10001 -m analyst
COPY b1 ./b1
COPY b2 ./b2
COPY examples ./examples
COPY scripts ./scripts
RUN mkdir runs && chown analyst:analyst runs
USER analyst
ENV MPLCONFIGDIR=/tmp/matplotlib
ENTRYPOINT ["python", "-m", "b1"]
CMD ["demo"]
