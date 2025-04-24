#!/bin/bash

# CRWLR Conda Environment Setup Script
# Creates a conda environment based on pyproject.toml and requirements.txt

# Configuration
ENV_NAME="crwlr"
PYTHON_VERSION="3.11"  # Changed from 3.12 to 3.11 for better compatibility with cloud-sql-python-connector

echo "======================================================"
echo "  CRWLR Conda Environment Setup"
echo "======================================================"
echo "  - Environment name: $ENV_NAME"
echo "  - Python version: $PYTHON_VERSION"
echo "======================================================"

# Check if conda is available
if ! command -v conda &> /dev/null; then
    echo "Error: conda is not installed or not in PATH"
    echo "Please install Miniconda or Anaconda first"
    exit 1
fi

# Check if environment already exists
if conda env list | grep -q "^$ENV_NAME "; then
    echo "Environment '$ENV_NAME' already exists."
    read -p "Do you want to remove and recreate it? (y/N): " RECREATE
    if [[ $RECREATE == "y" || $RECREATE == "Y" ]]; then
        echo "Removing existing environment..."
        conda env remove -n $ENV_NAME
    else
        echo "Setup canceled. Using existing environment."
        exit 0
    fi
fi

# Create conda environment with Python version from pyproject.toml
echo "Creating conda environment '$ENV_NAME' with Python $PYTHON_VERSION..."
conda create -n $ENV_NAME python=$PYTHON_VERSION pip -y

# Activate environment
echo "Activating environment..."
eval "$(conda shell.bash hook)"
conda activate $ENV_NAME

# Install dependencies from requirements.txt
echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt

# Install playwright browsers
echo "Installing Playwright browsers..."
python -m playwright install

# Install development dependencies for code formatting and type checking
echo "Installing development dependencies for code quality..."
pip install black isort mypy

# Install the package in development mode
echo "Installing the package in development mode..."
pip install -e .

# Install PostgreSQL client (useful for database tools)
echo "Installing PostgreSQL client..."
conda install -c conda-forge postgresql -y

echo "======================================================"
echo "  CRWLR Conda Environment Setup Complete!"
echo "======================================================"
echo "  To activate the environment:"
echo "    conda activate $ENV_NAME"
echo ""
echo "  To start the FastAPI server:"
echo "    uvicorn app.main:app --reload"
echo "======================================================" 