#!/usr/bin/env python
# Upload functions for stomata server

import os
import db
from config import config
from flask import request, redirect
from werkzeug.utils import secure_filename
from zipfile import ZipFile
from webapp_users import get_current_user_id
from PIL import Image
from webapp_base import error_redirect, set_error, set_notice

# Upload
def upload_file(dataset_id, image_zoom=None, threshold_prob=None, allow_reuse=False):
    # check if the post request has the file part
    if 'file' not in request.files: return error_redirect('No file.')
    file = request.files['file']
    if file.filename == '': return error_redirect('Empty file.')
    if (not file): return error_redirect('Invalid file.')
    # Generate server-side filename
    basename, ext = os.path.splitext(secure_filename(file.filename))
    filename = basename + ext
    # Generate dataset if needed
    is_new_dataset = (dataset_id is None)
    if is_new_dataset:
        dataset_id = db.add_dataset(name=filename, user_id=get_current_user_id(), image_zoom=image_zoom,
                                    threshold_prob=threshold_prob)['_id']
    if allow_reuse:
        db.add_dataset_tag(dataset_id, 'reuse')
    # Handle according to type
    if ext in config.archive_extensions:
        return upload_archive(dataset_id, file, filename, is_new_dataset=is_new_dataset)
    elif ext in config.image_extensions:
        return upload_image(dataset_id, file, filename, is_new_dataset=is_new_dataset)
    else:
        return error_redirect('Invalid or disallowed file type.')


def make_unique_server_image_filename(filename):
    basename, ext = os.path.splitext(filename)
    filename = basename + ext
    i = 1
    while True:
        full_fn = os.path.join(config.get_server_image_path(), filename)
        if not os.path.isfile(full_fn):
            break
        filename = '%s-%03d%s' % (basename, i, ext)
        i += 1
    if i > 1:
        print('File renamed to be unique:', filename)
    return full_fn


def upload_archive(dataset_id, file, _filename, is_new_dataset):
    zipf = ZipFile(file.stream, 'r')
    print('ZIPF:')
    for nfo in zipf.infolist():
        if nfo.file_size > config.max_image_file_size:
            return error_redirect('One of the images (%s) exceeds the max file size (%d).' % (nfo.filename, config.max_image_file_size))
    n_added = 0
    for nfo in zipf.infolist():
        # Check
        image_filename = secure_filename(nfo.filename)
        basename, ext = os.path.splitext(image_filename)
        if ext not in config.image_extensions:
            print('Not an image: %s (%s)' % (ext, image_filename))
            continue
        # Extract
        full_fn = make_unique_server_image_filename(image_filename)
        zfile = zipf.open(nfo)
        with open(full_fn, 'wb') as fid:
            contents = zfile.read()
            fid.write(contents)
        print('Written to ', full_fn)
        # Add
        if add_uploaded_image(dataset_id, full_fn, image_filename) is not None:
            n_added += 1
        else:
            # Cleanup invalid
            if os.path.isfile(full_fn):
                os.remove(full_fn)
    set_notice('%d images added.' % n_added)
    ds_url_suffix = '?new=true' if is_new_dataset else ''
    return redirect('/dataset/%s' % str(dataset_id) + ds_url_suffix, 200)


def upload_image(dataset_id, imfile, filename, is_new_dataset):
    # Make unique filename
    full_fn = make_unique_server_image_filename(filename)
    # Save it
    imfile.save(full_fn)
    # Process
    entry = add_uploaded_image(dataset_id, full_fn, filename)
    # Redirect to file info
    #return redirect('/info/%s' % str(entry['_id']))
    ds_url_suffix = '?new=true' if is_new_dataset else ''
    return redirect('/dataset/%s' % str(dataset_id) + ds_url_suffix, 200)


def add_uploaded_image(dataset_id, full_fn, filename):
    # Get some image info
    try:
        im = Image.open(full_fn)
    except:
        print('Invalid image file: %s' % full_fn)
        set_error('Could not load image. Invalid / Upload error?')
        return None
    # Add DB entry (after file save to worker can pick it up immediately)
    entry = db.add_sample(name=filename, filename=os.path.basename(full_fn), size=im.size, dataset_id=dataset_id)
    # Return added entry
    return entry
