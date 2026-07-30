#!/bin/sh
set -eu

TOKEN="${WHISPER_TOKEN:-}"

if [ -z "$TOKEN" ]; then
    echo "Error: WHISPER_TOKEN is empty or not set." >&2
    exit 1
fi

# Calculate character length
TOKEN_LEN=$(printf '%s' "$TOKEN" | wc -c | tr -d ' ')

if [ "$TOKEN_LEN" -lt 32 ] || [ "$TOKEN_LEN" -gt 128 ]; then
    echo "Error: WHISPER_TOKEN must be between 32 and 128 characters long." >&2
    exit 1
fi

# POSIX case pattern allowlist check: reject any character outside [A-Za-z0-9._~-]
case "$TOKEN" in
    *[!A-Za-z0-9._~-]* | *"
"*)
        echo "Error: WHISPER_TOKEN contains invalid characters. Must contain only A-Z, a-z, 0-9, ., _, ~, -" >&2
        exit 1
        ;;
esac

TEMPLATE_FILE="${TEMPLATE_FILE:-/etc/alertmanager/alertmanager.yml}"
TARGET_FILE="${TARGET_FILE:-/tmp/alertmanager.yml}"

if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "Error: Alertmanager template file $TEMPLATE_FILE not found." >&2
    exit 1
fi

TMP_TARGET="${TARGET_FILE}.tmp.$$"
trap 'rm -f "$TMP_TARGET"' EXIT INT TERM

umask 077
touch "$TMP_TARGET"
chmod 600 "$TMP_TARGET"

while IFS= read -r line; do
    rest="$line"
    while true; do
        case "$rest" in
            *__WHISPER_TOKEN__*)
                before="${rest%%__WHISPER_TOKEN__*}"
                after="${rest#*__WHISPER_TOKEN__}"
                printf '%s' "$before"
                printf '%s' "$TOKEN"
                rest="$after"
                ;;
            *)
                printf '%s\n' "$rest"
                break
                ;;
        esac
    done
done < "$TEMPLATE_FILE" > "$TMP_TARGET"
chmod 600 "$TMP_TARGET"

mv -f "$TMP_TARGET" "$TARGET_FILE"
chmod 600 "$TARGET_FILE"
trap - EXIT INT TERM

if [ "${1:-}" = "validate-only" ]; then
    echo "Validation and rendering successful."
    exit 0
fi

exec /bin/alertmanager --config.file="$TARGET_FILE" --storage.path=/alertmanager "$@"
