from setuptools import setup, find_packages

name = "streamglob_scraper_sample"
setup(
    name=name,
    version="1.0",

    description="...",

    author="...",
    author_email="...",

    platforms=["Any"],
    packages=["sample"],
    include_package_data=True,

    entry_points={
        "streamglob.scrapers": [
            "sample = sample:SampleScraper"
        ],
    },
    zip_safe=False
)
