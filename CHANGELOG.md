# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

### Fixed

### Changed
- Merge 5-10k bucket with 2-5k bucket (#22)
- Add Openverse integration for finding diverse replacement images (#21)
- Add link to source page on thumbnails (#20)
- Split 'not applicable' into more specific statuses (#19)
- Build test infrastructure for career-images scripts (#18)
- Create tool to audit previously-added images: identify which uploaded images are no longer in their articles (#15)
- Ensure tool output is web-accessible (investigate if Google Sheets integration is sufficient or if additional web interface is needed) (#14)
- Upload wizard link should use attribution from source photo/Openverse (#13)
- Sort by pageview buckets, alphabetically within bucket (#12)
- Add Google Sheets integration for tracking reviewed articles (#11)
- Fix Commons upload wizard link - parameters not working (#10)
- Find or write Wikipedia essay on NPOV (Neutral Point of View) when applied to images/pictures (#9)
- Rename statuses: needs_image → needs_diverse_images, has_image → has_diverse_images (#7)
- Convert project to use uv for dependency management (#6)
- Add license metadata to thumbnails (#5)
- Add quick review mode: given an article name, display image thumbnails and metadata for rapid review (#3)
