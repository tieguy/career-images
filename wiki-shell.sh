#!/bin/sh
# SPDX-FileCopyrightText: 2025 Wikipedia Career Images Contributors
# SPDX-License-Identifier: MPL-2.0

# Check if container is running, start if not
if ! podman ps -q -f name=wikipedia-career-images-container | grep -q .; then
    echo "Container not running, starting devcontainer..."
    devcontainer up --workspace-folder "$(dirname "$0")"
fi

podman exec -it -u vscode -w /workspaces/wikipedia-career-images wikipedia-career-images-container bash
