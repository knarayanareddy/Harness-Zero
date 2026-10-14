# Tirith Security Capsule Policy (`policy.tirith`)

Audits tool calls at policy order 60:
- Blocks commands leaking secrets (AWS keys, GitHub tokens, OpenAI/Anthropic keys, private SSH keys, JWTs).
- Blocks network targets targeting cloud metadata (169.254/16, Google internal, Alibaba), loopback (127.0.0.0/8, localhost), and RFC1918 private subnets (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16).
- Blocks destructive blast-radius commands (rm -rf, git reset --hard, git push --force, dd, sudo, mkfs).
- Raises moderate blast-radius commands (single-file rm, permission modifications) to `DESTRUCTIVE` risk class.
