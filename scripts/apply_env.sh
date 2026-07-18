#!/usr/bin/env bash
# Description: Applies system-wide environment variables from a template
# Execution: ./apply_env.sh

set -e

TEMPLATE_FILE="./system-configs/etc_environment.template"
TARGET_FILE="/etc/environment"

if [[ ! -f "$TEMPLATE_FILE" ]]; then
    echo "Error: Template not found at $TEMPLATE_FILE"
    exit 1
fi

echo "Overwriting $TARGET_FILE with $TEMPLATE_FILE..."

# tee -a appends, tee without -a overwrites. 
# > /dev/null suppresses standard output so it doesn't flood the terminal.
cat "$TEMPLATE_FILE" | sudo tee "$TARGET_FILE" > /dev/null

echo "Environment written successfully."
echo "Note: System-wide environment changes require a reboot or a new login session to take effect."