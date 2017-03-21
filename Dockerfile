# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.

FROM ubuntu:16.04

USER root
# env
ENV DEBIAN_FRONTEND noninteractive

ENV VALHALLA_VERSION "2.1.4"

ENV CONDA_DIR /opt/conda
ENV PATH $CONDA_DIR/bin:$PATH

# Install all OS dependencies for fully functional notebook server
RUN apt-get update && apt-get install -yq --no-install-recommends \
    git \
    vim \
    jed \
    emacs \
    wget \
    build-essential \
    python-dev \
    unzip \
    libsm6 \
    pandoc \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-extra \
    texlive-fonts-recommended \
    texlive-generic-recommended \
    texlive-xetex \
    libxrender1 \
    inkscape \
    software-properties-common \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN cd /tmp && \
    mkdir -p $CONDA_DIR && \
    wget --quiet https://repo.continuum.io/miniconda/Miniconda3-4.2.12-Linux-x86_64.sh && \
    echo "c59b3dd3cad550ac7596e0d599b91e75d88826db132e4146030ef471bb434e9a *Miniconda3-4.2.12-Linux-x86_64.sh" | sha256sum -c - && \
    /bin/bash Miniconda3-4.2.12-Linux-x86_64.sh -f -b -p $CONDA_DIR && \
    rm Miniconda3-4.2.12-Linux-x86_64.sh && \
    $CONDA_DIR/bin/conda config --system --add channels conda-forge && \
    $CONDA_DIR/bin/conda config --system --set auto_update_conda false && \
    conda clean -tipsy

# Install Jupyter Notebook and Hub
RUN conda install --quiet --yes \
    'notebook=4.4.*' \
    'jupyterhub=0.7.*' \
    && conda clean -tipsy


RUN apt-add-repository -y ppa:kevinkreiser/prime-server
RUN apt-add-repository -y ppa:valhalla-routing/valhalla
RUN apt-get update && apt-get install -y libvalhalla${VALHALLA_VERSION}-dev \
      valhalla${VALHALLA_VERSION}-bin \
      python-valhalla${VALHALLA_VERSION}

RUN apt-get -y install dh-autoreconf
RUN apt-get -y install protobuf-compiler

RUN apt-get -y install python-redis \
               python-requests

WORKDIR /osmlr

RUN git clone --recursive https://github.com/opentraffic/osmlr.git .
RUN sh autogen.sh
RUN ./configure
RUN make && make install

# Install Python 3 packages
RUN conda install --quiet --yes \
    'ipywidgets=5.2*' \
    'ipyleaflet' && \
    conda clean -tipsy

# Activate ipywidgets extension in the environment that runs the notebook server
RUN jupyter nbextension enable --py widgetsnbextension --sys-prefix

# Install Python 2 packages
RUN conda create --quiet --yes -p $CONDA_DIR/envs/python2 python=2.7 \
    'ipython=4.2*' \
    'ipywidgets=5.2*' \
    'ipyleaflet' \
    'pyshp'  \
    'shapely' && \
    conda clean -tipsy

# Add shortcuts to distinguish pip for python2 and python3 envs
RUN ln -s $CONDA_DIR/envs/python2/bin/pip $CONDA_DIR/bin/pip2 && \
    ln -s $CONDA_DIR/bin/pip $CONDA_DIR/bin/pip3


RUN jupyter nbextension enable --py --sys-prefix ipyleaflet


# Install Python 2 kernel spec globally to avoid permission problems when NB_UID
# switching at runtime and to allow the notebook server running out of the root
# environment to find it. Also, activate the python2 environment upon kernel
# launch.
RUN pip install kernda --no-cache && \
    $CONDA_DIR/envs/python2/bin/python -m ipykernel install && \
    kernda -o -y /usr/local/share/jupyter/kernels/python2/kernel.json && \
    pip uninstall kernda -y


# cleanup
RUN apt-get clean && \
      rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

CMD jupyter notebook --ip=* /jupyter/
