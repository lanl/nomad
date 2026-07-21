# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added the `--report` flag to `nomad export` to generate model cards and a linked README for the configured models and tools ([#3])

## Changed

- Migrated gateway upstream connections and transport configuration to FastMCP ([#4])

### Fixed

- Fixed borked nomad executable path in Nomad Demo Dockerfile ([#2])

## [v0.1.0] - 2026-07-13

Initial Public Release of Nomad

<!-- Versions -->
[unreleased]: https://github.com/lanl/nomad/commits/main
[v0.1.0]: https://github.com/lanl/nomad/tree/v0.1.0

<!-- Pull Requests -->
[#2]: https://github.com/lanl/nomad/pull/2
[#3]: https://github.com/lanl/nomad/pull/3
[#4]: https://github.com/lanl/nomad/pull/4
