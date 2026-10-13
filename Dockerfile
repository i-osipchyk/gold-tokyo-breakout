# One image for every function; Terraform picks the handler per function
# (gtb.runs.app.{morning,heartbeat,eod,watchdog}_handler). CI builds it for arm64:
#   docker buildx build --platform linux/arm64 --build-arg GIT_SHA=$(git rev-parse HEAD) -t gtb .

FROM public.ecr.aws/lambda/python:3.12 AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt \
    && uv pip install --no-cache --python /var/lang/bin/python --target /deps -r requirements.txt

FROM public.ecr.aws/lambda/python:3.12
COPY --from=build /deps ${LAMBDA_TASK_ROOT}
COPY data/early_close.yaml ${LAMBDA_TASK_ROOT}/data/early_close.yaml
COPY src/gtb ${LAMBDA_TASK_ROOT}/gtb

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} \
    GTB_CALENDAR=${LAMBDA_TASK_ROOT}/data/early_close.yaml \
    PYTHONWARNINGS="ignore:You do not have a working installation of the service_identity module"

CMD ["gtb.runs.app.morning_handler"]
