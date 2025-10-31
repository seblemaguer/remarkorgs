# remarkorgs

<p style="text-align:center;">
**The name of the project is likely to change soon as it is in early development at the moment**
</p>


This project is a wrapper around [remarks](https://github.com/Scrybbling-together/remarks).
remarks is aimed to be integrated to the scrybble pipeline but contains a lot of things that I need.
Therefore, the goal of this wrapper is to process what I want, while keeping maximumal dependency to remarks but without impacting its core.

## How to install

Just clone the repository and run the following command:

```sh
pip install -e .
```

To install the development environment, run the following command:

```sh
pip install -e .[dev]
```

## How to run

```sh
usage: remarkorgs [-h] [-l LOG_FILE] [-v] [-O] input_dir output_dir

positional arguments:
  input_dir             xochitl directory sync from the remarkable
  output_dir            base directory which will contains all the extract files

options:
  -h, --help            show this help message and exit
  -l LOG_FILE, --log_file LOG_FILE
                        Logger file
  -v, --verbosity       increase output verbosity
  -O, --override        Override existing files (/!\ use at your own risk!)
```
