#!/bin/bash

IMAGE_NAME="oci-usage-cost"
REGISTRY="registry.local.timmybtech.com"
TAG="latest"
PLATFORM="linux/amd64"
ARGOCD_APP_NAME="oci-usage-cost"
ARGOCD_SERVER="argocd.local.timmybtech.com"
DEPLOYMENT_NAME="oci-usage-cost"
NAMESPACE="oci-usage-cost"
FULL_IMAGE_NAME="$REGISTRY/$IMAGE_NAME:$TAG"

msg_info() {
    echo -e "\033[1;34m[INFO]\033[0m $1"
}

msg_ok() {
    echo -e "\033[1;32m[OK]\033[0m $1"
}

msg_warn() {
    echo -e "\033[1;33m[WARN]\033[0m $1"
}

msg_error() {
    echo -e "\033[1;31m[ERROR]\033[0m $1"
}

handle_error() {
    msg_error "$1"
    exit 1
}

if [ "$1" = "skip" ]; then
    msg_info "Skipping pre-deployment checks..."
else
    msg_info "Checking for uncommitted changes..."
    if [[ -n $(git status --porcelain) ]]; then
        handle_error "Uncommitted changes detected. Please commit or stash them before deploying."
    fi

    msg_info "Checking if on main branch..."
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    if [[ "$BRANCH" != "main" ]]; then
        handle_error "You must be on the main branch to deploy (current: $BRANCH)."
    fi
fi

build_and_push_image() {
    msg_info "Building image for platform $PLATFORM..."
    if ! podman build --platform $PLATFORM -t $IMAGE_NAME . --no-cache; then
        handle_error "Failed to build image."
    fi

    msg_info "Tagging image..."
    if ! podman tag $IMAGE_NAME $FULL_IMAGE_NAME; then
        handle_error "Failed to tag image."
    fi

    msg_info "Pushing image to registry..."
    if ! podman push $FULL_IMAGE_NAME; then
        handle_error "Failed to push image."
    fi
}

build_and_push_image

msg_ok "Deployment script completed successfully!"
