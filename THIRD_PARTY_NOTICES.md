# Third-party data and software notices

The MIT license in this repository applies to the original project code and documentation. It does not replace the licenses of third-party datasets, upstream research code, Python packages, or hosted model services.

## UCI Online Retail

- Creator: Daqing Chen
- Citation: Chen, D. (2015). *Online Retail* [Dataset]. UCI Machine Learning Repository.
- DOI: https://doi.org/10.24432/C5BW33
- Source: https://archive.ics.uci.edu/dataset/352/online+retail
- License: Creative Commons Attribution 4.0 International (CC BY 4.0), https://creativecommons.org/licenses/by/4.0/

The repository does not redistribute the original workbook. `scripts/acquire_retail.py` downloads it from the official UCI location and records source metadata and SHA-256 hashes. The HTML demonstration is a derived analytical report and identifies the source, license, aggregation definitions, and limitations.

## Many Labs 1

- Project: ManyLabsOpenScience/ManyLabs1
- Pinned commit: `7a1334c053201ccdd2054417f74aa3d62776dd03`
- Source: https://github.com/ManyLabsOpenScience/ManyLabs1
- Study DOI: https://doi.org/10.1027/1864-9335/a000178
- Data paper DOI: https://doi.org/10.5334/jopd.ad
- Data license: CC0 1.0, as stated in the upstream file `Data/Data released under CC0 license.txt`
- Upstream analysis-code license: GNU GPL v3, as stated in the upstream `LICENSE`

The upstream files are not committed to this repository. Acquisition scripts can retrieve the pinned source materials for reproducibility. Upstream analysis code is preserved as third-party material when downloaded and is not relicensed as MIT or imported into the original project package.

## Python dependencies and model APIs

Python packages installed from `requirements*.txt` retain their respective upstream licenses and are not redistributed in this repository. Model names and API endpoints identify compatible services and do not imply endorsement. Users are responsible for the terms, data-processing rules, and charges of the provider they configure.
