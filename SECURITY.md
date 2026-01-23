# Security Policy

## Supported Versions

The following versions of Computational Linguistic Stratigraphy are currently being supported with security updates:

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

We take the security of Computational Linguistic Stratigraphy seriously. If you believe you have found a security vulnerability, please report it to us as described below.

### How to Report

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to the maintainers. You can find maintainer contact information in the repository.

If you prefer, you can also use GitHub's private vulnerability reporting feature:
1. Go to the repository's Security tab
2. Click "Report a vulnerability"
3. Fill out the form with details about the vulnerability

### What to Include

Please include the following information in your report:

- **Type of issue** (e.g., buffer overflow, SQL injection, cross-site scripting, etc.)
- **Full paths of source file(s)** related to the manifestation of the issue
- **Location of the affected source code** (tag/branch/commit or direct URL)
- **Step-by-step instructions** to reproduce the issue
- **Proof-of-concept or exploit code** (if possible)
- **Impact of the issue**, including how an attacker might exploit it

### Response Timeline

- **Initial Response**: We will acknowledge receipt of your vulnerability report within 48 hours.
- **Status Update**: We will provide a more detailed response within 7 days, indicating the next steps in handling your report.
- **Resolution**: We aim to resolve critical vulnerabilities within 30 days of disclosure.

### What to Expect

- We will confirm receipt of your vulnerability report
- We will investigate and validate the reported vulnerability
- We will work on a fix and coordinate disclosure timing with you
- We will publicly acknowledge your responsible disclosure (unless you prefer to remain anonymous)

## Security Best Practices

When deploying Computational Linguistic Stratigraphy, please follow these security recommendations:

### Configuration

- **Environment Variables**: Never commit `.env` files or secrets to version control
- **API Keys**: Use strong, unique API keys and rotate them regularly
- **Database Credentials**: Use strong passwords and restrict database access
- **Network**: Run services behind a firewall and use TLS for all connections

### Deployment

- **Docker**: Run containers as non-root users (our Dockerfile already configures this)
- **Updates**: Keep all dependencies and base images up to date
- **Access Control**: Implement proper authentication and authorization
- **Monitoring**: Enable logging and monitor for suspicious activity

### Development

- **Dependencies**: Regularly audit dependencies for known vulnerabilities
  ```bash
  make security-check
  ```
- **Secrets**: Use environment variables or secret management tools
- **Input Validation**: Always validate and sanitize user input
- **Code Review**: All changes should go through code review

## Security Features

This project includes several security measures:

- **Input Validation**: Pydantic models validate all input data
- **Rate Limiting**: API endpoints are rate-limited to prevent abuse
- **Authentication**: API key authentication for protected endpoints
- **Secrets Management**: Sensitive configuration via environment variables
- **Security Scanning**: Automated Bandit security scanning in CI/CD
- **Dependency Updates**: Dependabot monitors for vulnerable dependencies

## Security Updates

Security updates will be released as patch versions and announced through:

- GitHub Security Advisories
- Release notes in CHANGELOG.md
- GitHub Releases

We recommend enabling GitHub notifications for this repository to receive security announcements.

## Acknowledgments

We appreciate the security research community's efforts in helping keep this project secure. Responsible disclosure of vulnerabilities helps us ensure the security and privacy of all users.

Thank you for helping to keep Computational Linguistic Stratigraphy secure!
