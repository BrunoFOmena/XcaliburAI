# ECHOLAS pipeline image (issue #4): every tool the five modules run, at the versions
# locked in ../src/xcalibur_echolas/workflow/envs/*.linux-64.lock.
#
# Build from packages/xcalibur-echolas (see README):
#   docker build -f docker/Dockerfile -t ghcr.io/brunofomena/xcalibur-echolas:<version> .
#
# Written for XcaliburAI from the tools the modules use; ECHOLAS (MIT,
# https://github.com/MCamposDA/ECHOLAS) has its own Docker files, which were not copied.

FROM mambaorg/micromamba:2.0.5-debian12-slim

ARG VERSION=0.0.0
LABEL org.opencontainers.image.title="xcalibur-echolas" \
      org.opencontainers.image.description="Tools of the ECHOLAS pipeline (XcaliburAI)" \
      org.opencontainers.image.source="https://github.com/BrunoFOmena/XcaliburAI" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later AND MIT"

ENV ECHOLAS_HOME=/opt/echolas
COPY --chown=$MAMBA_USER:$MAMBA_USER src/xcalibur_echolas/workflow/envs/ ${ECHOLAS_HOME}/envs/

# Explicit locks: the same packages, builds and checksums on every build.
RUN micromamba create -y -n echolas -f ${ECHOLAS_HOME}/envs/echolas.linux-64.lock \
 && micromamba create -y -n echolas-pca -f ${ECHOLAS_HOME}/envs/echolas-pca.linux-64.lock \
 && micromamba clean --all --yes

# The main environment is on PATH. The PCA step (RUN_PCA) puts echolas-pca first through
# the docker profile of nextflow.config, because GATK's package brings its own older R.
# Nextflow runs tasks as the calling user (docker profile), who may have no home here.
ENV PATH=/opt/conda/envs/echolas/bin:/opt/conda/bin:$PATH \
    HOME=/tmp \
    LC_ALL=C.UTF-8

COPY --chown=$MAMBA_USER:$MAMBA_USER docker/check-tools.sh ${ECHOLAS_HOME}/check-tools.sh
# Fails the build if any tool is missing or at another version; keeps the list in the image.
RUN bash ${ECHOLAS_HOME}/check-tools.sh ${ECHOLAS_HOME}/envs > ${ECHOLAS_HOME}/tool-versions.tsv \
 && cat ${ECHOLAS_HOME}/tool-versions.tsv
