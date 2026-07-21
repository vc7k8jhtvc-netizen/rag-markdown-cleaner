from setuptools import find_packages, setup


setup(
    name="rag-cleaner",
    version="1.3.0",
    description=(
        "面向中级注册安全工程师 AI 学习知识库的 "
        "Markdown 批量清洗工具"
    ),
    long_description=open(
        "README.md",
        encoding="utf-8-sig",
    ).read(),
    long_description_content_type="text/markdown",
    packages=find_packages(
        include=[
            "clean_auto",
            "clean_auto.*",
        ]
    ),
    python_requires=">=3.10",
    install_requires=[
        "httpx>=0.27.0,<1.0",
        "python-dotenv>=1.0.0,<2.0",
        "PyYAML>=6.0,<7.0",
    ],
    entry_points={
        "console_scripts": [
            "rag-cleaner=clean_auto.pipeline:run",
        ],
    },
    include_package_data=True,
)
