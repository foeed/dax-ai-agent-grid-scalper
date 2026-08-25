# Contributing to DAX AI Agent Grid Scalper

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How to Contribute](#how-to-contribute)
- [Development Setup](#development-setup)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)

## Code of Conduct

Please read our [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before contributing. We expect all contributors to follow it.

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/foeed/dax-ai-agent-grid-scalper/issues) to avoid duplicates
2. Open a new issue using the **Bug Report** template
3. Include: MT5 version, Python version, OS, steps to reproduce, logs

### Suggesting Features

1. Open a new issue using the **Feature Request** template
2. Describe the use case and expected behavior
3. Tag it with `enhancement`

### Submitting Code

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes
4. Test thoroughly on demo account
5. Commit with a descriptive message
6. Push and open a Pull Request

## Development Setup

### Prerequisites

- Python 3.11+
- MetaTrader 5 (for EA testing)
- Git

### Backend Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/dax-ai-agent-grid-scalper.git
cd dax-ai-agent-grid-scalper/Backend

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Copy environment template
copy .env.example .env
# Edit .env with your API keys

# Run the server
python -m uvicorn app.main:app --reload
```

### EA Setup

1. Copy `.mq5` files to your MT5 `MQL5/Experts/` folder
2. Copy `Include/` files to your MT5 `MQL5/Include/` folder
3. Compile in MetaEditor

## Pull Request Process

### Before Submitting

- [ ] Code compiles without errors (MQL5) or passes lint (Python)
- [ ] No hardcoded API keys or secrets
- [ ] Updated documentation if needed
- [ ] Tested on demo account (for EA changes)
- [ ] Follows existing code style

### PR Guidelines

- Use a clear, descriptive title
- Reference related issues: `Fixes #123`
- Keep changes focused �?" one feature/fix per PR
- Add screenshots for UI changes (dashboard modifications)

### Review Process

1. Maintainer reviews within 48 hours
2. Address feedback promptly
3. Once approved, maintainer will merge

## Coding Standards

### MQL5

- Use descriptive variable names
- Add comments for complex logic
- Follow MetaQuotes style guide
- Keep functions under 100 lines where possible

### Python

- Follow PEP 8
- Use type hints
- Maximum line length: 120 characters
- Use `ruff` for linting: `ruff check .`

### General

- Never commit `.env` files or API keys
- Use meaningful commit messages
- Keep commits atomic and focused

## Questions?

Open an issue with the `question` label or start a [Discussion](https://github.com/foeed/dax-ai-agent-grid-scalper/discussions).
