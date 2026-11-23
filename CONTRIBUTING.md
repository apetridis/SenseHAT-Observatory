# Contributing

Thank you for considering a contribution to SenseHAT Observatory.

## Before making a change

- Search existing issues and pull requests before starting substantial work.
- For larger changes, open an issue to describe the problem and proposed
  direction. Small fixes and documentation improvements can go directly to a
  pull request.
- Keep pull requests focused and explain the user-visible behavior and design
  tradeoffs.

## Development setup

Python unit tests and static deployment checks can run on a development
computer without Sense HAT hardware:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
pytest
python -m compileall -q src tests scripts
python scripts/generate_dashboard.py
docker compose --env-file .env.example --project-directory "$PWD" \
  -f deploy/compose.yaml config --quiet
```

Do not run the Pi installer on a development machine. It installs OS packages,
creates a system user and service, and starts containers on the deployment
host.

## Hardware changes

- Keep hardware access behind `SensorInterface` so unit tests can use fakes.
- Do not assume sensor units or joystick behavior: cite the official API and
  document units in field names, code, and README.
- Add mocked tests for calculations, conversions, errors and event handling.
- Clearly mark physical Raspberry Pi/Sense HAT checks as pending unless they
  were actually performed.
- Leave LED output disabled by default; avoid repeated writes that overwrite
  user content.

## Configuration and security

- Do not commit credentials, private configuration, precise location metadata,
  LAN addresses, logs, or data backups.
- Put safe, non-secret defaults in `config/observatory.example.yaml` and
  `.env.example`.
- Validate new configuration values and document their units and bounds.
- Bind network services only to the configured local interface. Do not add
  public port forwarding, privileged ports, or changes to host firewall/network
  configuration.
- Keep Docker volumes persistent and document data migration or retention
  changes.

## Pull requests

Before submitting:

1. Run the tests and checks in Development setup.
2. Update relevant documentation and examples.
3. Describe tests run and list any physical-hardware checks still needed.
4. Confirm the change does not add secrets or machine-specific settings.

Contributions are licensed under the repository's MIT License.
