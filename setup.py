from setuptools import find_packages, setup

setup(
    name="rag-cleaner",
    version="1.2.0",
    description="RAG Markdown 批量清洗工具",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "httpx>=0.27.0",
        "python-dotenv>=1.0.0",
        "PyYAML>=6.0",
    ],
    entry_points={
        "console_scripts": [
            "rag-cleaner=clean_auto.pipeline:run",
        ],
    },
    include_package_data=True,
)
