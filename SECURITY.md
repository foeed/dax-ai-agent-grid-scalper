# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public GitHub issue** for security vulnerabilities
2. Email the maintainer directly or use GitHub's private vulnerability reporting
3. Include a description of the vulnerability and steps to reproduce
4. Allow reasonable time for a fix before public disclosure

## API Key Safety

This project uses API keys for DeepSeek AI and NewsAPI. Please follow these practices:

### DO

- Use `.env.example` as a template, never commit `.env`
- Store API keys in environment variables or `.env` files only
- Use different API keys for development and production
- Rotate keys regularly
- Monitor API usage for unauthorized access

### DON'T

- Never hardcode API keys in `.mq5`, `.py`, or any source file
- Never commit `.env` files to version control
- Never share API keys in public channels, issues, or pull requests
- Never log API keys to output files

### If You Accidentally Commit a Key

1. Immediately revoke the compromised key from the provider's dashboard
2. Generate a new key
3. Remove the key from git history: `git filter-branch --force --index-filter 'git rm --cached --ignore-unmatch Backend/.env' HEAD`
4. Force push: `git push --force`

## MetaTrader 5 Security

- Only enable "Allow WebRequest" for trusted API domains
- Never run EAs with real money on unverified code
- Test all changes on demo accounts first
- Review EA permissions before deployment

## Dependency Security

- Keep Python dependencies updated: `pip install --upgrade -r requirements.txt`
- Monitor for vulnerabilities: `pip audit`
- Docker images should use official Python base images

## Scope

This security policy applies to the latest version of DAX V2 on the `main` branch.
