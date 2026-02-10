# Shared environment bootstrap for Makefiles.
# Resolves PRIVATE_DIR, loads secret env files, and exports selected variables
# so sub-makes and helper scripts see a consistent configuration.

ifeq ($(origin __ENV_MK_INCLUDED__), undefined)
__ENV_MK_INCLUDED__ := yes

ENV_MK_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
ENV_MK_DIR := $(dir $(ENV_MK_PATH))
REPO_ROOT ?= $(abspath $(ENV_MK_DIR)/..)

ifeq ($(origin PRIVATE_DIR), undefined)
  ifneq ("$(wildcard $(REPO_ROOT)/.privatedir)","")
    PRIVATE_DIR := $(shell cat $(REPO_ROOT)/.privatedir)
  else
    PRIVATE_DIR := $(abspath $(REPO_ROOT)/private)
  endif
endif
export PRIVATE_DIR

COMMON_ENV ?= $(PRIVATE_DIR)/secrets/common.env
BACKEND_ENV ?= $(PRIVATE_DIR)/secrets/backend.env
FRONTEND_ENV ?= $(PRIVATE_DIR)/secrets/frontend.env

-include $(COMMON_ENV)
-include $(BACKEND_ENV)
-include $(FRONTEND_ENV)

MAKE_ENV_EXPORT_VARS ?= \
  PROJECT_ID REGION BUCKET_NAME INDEX_ID INDEX_ENDPOINT_ID DEPLOYED_INDEX_ID \
  CHUNKS_PATH API_KEY PERSONA_NAME MAX_INPUT_TOKENS MAX_OUTPUT_TOKENS REQ_TIMEOUT_MS \
  GOOGLE_APPLICATION_CREDENTIALS \
  DATAPOINTS_FILE DATAPOINTS_SCHEMA DATAPOINTS_INPUT DATAPOINTS_MODEL \
  DATAPOINTS_BATCH_SIZE DATAPOINTS_MAX_CHARS DATAPOINTS_GZIP DATAPOINTS_DIMENSIONS \
  ME_MIN_REPLICAS ME_MAX_REPLICAS

export $(MAKE_ENV_EXPORT_VARS)

BACKEND_DIR ?= $(REPO_ROOT)/backend
DOCKERFILE ?= $(BACKEND_DIR)/Dockerfile
LOCAL_IMAGE ?= persona-backend:local
AR_REPO ?= persona-llm
IMAGE_NAME ?= persona-backend
IMAGE_TAG ?= latest
IMAGE_URI ?= $(REGION)-docker.pkg.dev/$(PROJECT_ID)/$(AR_REPO)/$(IMAGE_NAME):$(IMAGE_TAG)

endif
