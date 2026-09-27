# Compliance Review

This document outlines the compliance considerations and data protection measures implemented in the Linguistic Stratigraphy project.

## Overview

Linguistic Stratigraphy processes primarily linguistic and etymological data from public sources. This review covers applicable regulations and best practices for data handling.

## Data Classification

### Data Types Processed

| Data Type | Classification | Source | PII Status |
|-----------|---------------|--------|------------|
| Etymological records | Public | Wiktionary, CLLD | No PII |
| Historical texts | Public/Academic | Corpora, Archives | Generally No PII |
| Language metadata | Public | ISO standards | No PII |
| API access logs | Internal | System-generated | May contain IP addresses |
| User accounts (if enabled) | Private | User-provided | Contains PII |

### Data Sensitivity Levels

1. **Public Data**: Etymological records, language data, historical texts from public sources
2. **Internal Data**: System logs, metrics, performance data
3. **Confidential Data**: API keys, database credentials, user authentication tokens
4. **Personal Data**: User account information (email, preferences) if user management is enabled

## GDPR Compliance

### Applicability

GDPR applies if:
- Processing personal data of EU residents
- Operating within the EU
- Offering services to EU users

### Implementation Measures

#### Data Minimization
- Only essential data is collected
- Etymological data contains no personal information
- API logs are anonymized/aggregated where possible

#### Right to Access (Article 15)
```python
# Implementation point: src/api/endpoints/user.py
GET /api/v1/user/data-export
```

#### Right to Erasure (Article 17)
```python
# Implementation point: src/api/endpoints/user.py
DELETE /api/v1/user/account
```

#### Data Portability (Article 20)
- Export formats: JSON, CSV
- Machine-readable data export available

#### Privacy by Design (Article 25)
- Encryption at rest for sensitive data
- Encryption in transit (TLS 1.2+)
- Access controls on all endpoints
- Audit logging for data access

### Technical Measures

| Measure | Implementation | Status |
|---------|---------------|--------|
| Encryption at rest | Database encryption | Configured |
| Encryption in transit | TLS/HTTPS required | Enforced |
| Access logging | Audit trail in PostgreSQL | Implemented |
| Data retention | Configurable retention periods | Implemented |
| Consent management | API-based consent tracking | Framework ready |

## HIPAA Compliance

### Applicability

HIPAA applies if processing Protected Health Information (PHI).

**Current Status**: Not applicable - Linguistic Stratigraphy does not process health data.

### Future Considerations

If health-related linguistic data is added:
- Implement BAA (Business Associate Agreement) framework
- Enable PHI-specific audit controls
- Configure HIPAA-compliant logging
- Implement minimum necessary access controls

## CCPA Compliance (California)

### Requirements Addressed

| Requirement | Implementation |
|-------------|---------------|
| Right to know | Data export endpoint |
| Right to delete | Account deletion endpoint |
| Right to opt-out | Preference management |
| Non-discrimination | Equal service regardless of privacy choices |

## Data Retention

### Retention Periods

| Data Type | Retention Period | Justification |
|-----------|-----------------|---------------|
| Etymological records | Indefinite | Public research data |
| API access logs | 90 days | Security and debugging |
| Error logs | 30 days | Debugging purposes |
| User accounts | Until deletion requested | Service provision |
| Metrics data | 1 year | Performance analysis |

### Deletion Procedures

```bash
# Log rotation (automated)
LOG_RETENTION_DAYS=90

# Manual data purge
make purge-old-logs
make purge-user-data USER_ID=<uuid>
```

## Security Controls

### Authentication & Authorization

- API key authentication for programmatic access
- JWT tokens for user sessions
- Role-based access control (RBAC)
- Rate limiting per API key

### Data Protection

- AES-256 encryption for data at rest
- TLS 1.2+ for data in transit
- Secrets stored in environment variables or secrets manager
- No credentials in source code

### Audit Trail

All data access is logged:
```json
{
  "timestamp": "2024-01-01T00:00:00Z",
  "user_id": "uuid",
  "action": "READ",
  "resource": "lsr",
  "resource_id": "uuid",
  "ip_address": "anonymized",
  "result": "success"
}
```

## Third-Party Services

### Data Processors

| Service | Purpose | Data Shared | DPA Status |
|---------|---------|-------------|------------|
| Cloud Provider | Infrastructure | All data | Required |
| Sentry | Error tracking | Error context | DPA available |
| Analytics (if used) | Usage metrics | Aggregated only | N/A |

### Data Transfer

- EU-US data transfers: Standard Contractual Clauses (SCCs) required
- Adequacy decisions: Check for country-specific requirements

## Incident Response

### Data Breach Procedure

1. **Detection**: Automated monitoring for anomalies
2. **Assessment**: Determine scope and impact
3. **Containment**: Isolate affected systems
4. **Notification**:
   - GDPR: 72 hours to supervisory authority
   - Users: Without undue delay if high risk
5. **Remediation**: Fix vulnerabilities
6. **Documentation**: Complete incident report

### Contact Information

- Data Protection Officer: [dpo@example.com]
- Security Team: [security@example.com]
- Privacy Inquiries: [privacy@example.com]

## Compliance Checklist

### Before Production Deployment

- [ ] Privacy policy published
- [ ] Terms of service published
- [ ] Cookie consent (if applicable)
- [ ] Data processing agreements with vendors
- [ ] Security assessment completed
- [ ] Penetration test conducted
- [ ] Incident response plan documented
- [ ] Staff training completed

### Ongoing Compliance

- [ ] Annual security review
- [ ] Quarterly access reviews
- [ ] Monthly log reviews
- [ ] Regular vulnerability scanning
- [ ] Compliance training updates

## Regulatory References

- **GDPR**: [Regulation (EU) 2016/679](https://gdpr-info.eu/)
- **CCPA**: [California Civil Code 1798.100-199](https://oag.ca.gov/privacy/ccpa)
- **HIPAA**: [45 CFR Parts 160 and 164](https://www.hhs.gov/hipaa/)
- **SOC 2**: [AICPA Trust Services Criteria](https://www.aicpa.org/)

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2024-01-01 | Initial compliance review |

---

*This document should be reviewed and updated annually or when significant changes occur to data processing activities.*
