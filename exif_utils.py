"""
Exiftool utilities for metadata extraction and injection.
Handles batch processing of image metadata using threading.
"""

import json
import subprocess
import threading
import os


def chunks(lst, n):
    """Split a list into chunks of size n"""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def ExiftoolGetMetadata(path, image, imageData):
    """Get metadata from exiftool for a single image"""
    cmd = ["exiftool", "-ee", "-G3", "-j", "{}{}{}".format(path, os.sep, image)]
    output = subprocess.run(cmd, capture_output=True)
    output = output.stdout.decode('utf-8', "ignore")
    photo = json.loads(output)[0]
    imageData[image] = photo


def ExiftoolGetImagesMetadata(path, images, imageData):
    """Get metadata from exiftool for multiple images using threading"""
    images = list(chunks(images, 5))

    for image in images:
        threads = []
        for i in range(0, len(image)):
            threads.append(threading.Thread(target=ExiftoolGetMetadata, args=(path, image[i], imageData,)))

        for t in threads:
            t.start()

        for t in threads:
            t.join()
    return imageData


def ExiftoolInjectMetadata(metadata):
    """Inject metadata into a single image using exiftool"""
    metadata.insert(0, "exiftool")
    output = subprocess.run(metadata, capture_output=True)
    if output.returncode == 0:
        print("Injecting additional metadata to {} is done.".format(metadata[-1]))
    else:
        print("Error Injecting additional metadata to {}.".format(metadata[-1]))


def ExiftoolInjectImagesMetadata(cmdMetaDataAll):
    """Inject metadata into multiple images using threading"""
    metadatas = list(chunks(cmdMetaDataAll, 5))

    for metadata in metadatas:
        threads = []
        for i in range(0, len(metadata)):
            threads.append(threading.Thread(target=ExiftoolInjectMetadata, args=(metadata[i],)))

        for t in threads:
            t.start()

        for t in threads:
            t.join()
    return
