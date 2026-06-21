from pathlib import Path

from setuptools import find_packages, setup


ROOT = Path(__file__).parent

setup(
    name="lumiforge",
    version="0.3.0",
    description="Evidence-backed Build Records for Coding Agent projects",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    author="Helia",
    license="MIT",
    url="https://github.com/lumihelia/LumiForge",
    project_urls={
        "Source": "https://github.com/lumihelia/LumiForge",
        "Issues": "https://github.com/lumihelia/LumiForge/issues",
    },
    packages=find_packages(),
    install_requires=[
        "watchdog>=3.0.0",
        "click>=8.0.0",
    ],
    entry_points={
        "console_scripts": [
            "lumiforge=lumiforge.cli:cli",
        ],
    },
    python_requires=">=3.10",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: End Users/Desktop",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    ],
)
