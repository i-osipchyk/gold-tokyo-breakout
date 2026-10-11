# One image for every function; Terraform picks the handler per function
# (gtb.runs.app.{morning,heartbeat,eod,watchdog,alarm}_handler). Build for arm64:
#   docker build --platform linux/arm64 --build-arg GIT_SHA=$(git rev-parse HEAD) -t gtb .
FROM public.ecr.aws/lambda/python:3.12

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && uv pip install --no-cache --python /var/lang/bin/python --target "${LAMBDA_TASK_ROOT}" \
       -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt pyproject.toml uv.lock

COPY data/early_close.yaml ${LAMBDA_TASK_ROOT}/data/early_close.yaml
COPY src/gtb ${LAMBDA_TASK_ROOT}/gtb

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} \
    GTB_CALENDAR=${LAMBDA_TASK_ROOT}/data/early_close.yaml \
    PYTHONWARNINGS="ignore:You do not have a working installation of the service_identity module"

CMD ["gtb.runs.app.morning_handler"]
