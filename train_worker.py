#!/usr/bin/env python3
# Retrain the Stomata identifier CNN

import os
import sys
import time
from config import config
import subprocess 
from archive2dataset import db2patches, patches2filelist
from convert_model_to_fcn import convert_epi1
import db
from bson.objectid import ObjectId
from subprocess import PIPE
import platform


def set_status(status_string):
    db.set_status('trainer', status_string)


def get_status():
    return db.get_status('trainer')


def imagelist2lmdb(image_root, imagelist_filename, lmdb_filename):
    print(('imagelist2lmdb to %s' % lmdb_filename))
    if os.path.exists(lmdb_filename):
        print ('Exists. Skipping.')
        return
    #Make sure that this path matches the location of your convert_imageset.exe file 
    #cnv_cmd = os.path.join(config.get_caffe_path(), 'tools', 'convert_imageset')
    if(platform.system()== 'Linux'): 
         cnv_cmd = os.path.join(config.get_caffe_path(),'convert_imageset')
    else: 
         cnv_cmd = os.path.join(config.get_caffe_path(), 'tools','Release', 'convert_imageset')
    cmd = [cnv_cmd, image_root + '/', imagelist_filename, lmdb_filename]
    

    #if subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True):
    #    raise RuntimeError('Error generating LMDB.')
    try:
        subprocess.check_output(cmd)
    except subprocess.CalledProcessError as e:
        raise RuntimeError("Error generating LMDB")
        #More detailed error
        #raise RuntimeError("command '{}' return with error (code {}): {}".format(e.cmd, e.returncode, e.output))
    print(('LMDB created at %s' % lmdb_filename))


def get_exec_path(model_id):
    exec_base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'cnn'))
    exec_path = os.path.join(exec_base_path, str(model_id))
    if not os.path.isdir(exec_path):
        os.makedirs(exec_path)
    return exec_path, exec_base_path


def remake_symlink(target_path, link_path):
    if os.path.isfile(link_path):
        os.remove(link_path)
    os.symlink(target_path, link_path)


def train(model_id):
    set_status('Train start for model %s' % str(model_id))
    exec_path, exec_base_path = get_exec_path(model_id)
    # Fetch model info
    model = db.get_model_by_id(model_id)
    def mset_status(s):
        set_status(model['name'] + ' ' + s)
    train_label = model['train_tag']
    # Prepare data
    mset_status('Prepare folder')
    output_path = os.path.join(config.get_train_data_path(), str(model_id), 'samples')
    if not os.path.isdir(output_path):
        os.makedirs(output_path)
    mset_status('Extracting patches')
    db2patches(output_path, train_label=train_label, sample_limit=model['sample_limit'])
    mset_status('Generating patch file list')
    patches2filelist(output_path)
    for dset in ('train', 'val', 'test'):
        mset_status('Preparing file list ' + dset)
        imagelist_filename = os.path.join(output_path, dset + '.txt')
        sample_count = len(open(imagelist_filename, 'rt').read().splitlines())
        db.set_model_parameters(model_id, {dset + '_count': sample_count})
        if not model.get('dataset_only', False):
            lmdb_filename = os.path.join(output_path, dset + '_lmdb')
            mset_status('Preparing lmdb ' + dset)
            imagelist2lmdb(output_path, imagelist_filename, lmdb_filename)
            exec_lmdb_filename = os.path.join(exec_path, dset + '_lmdb')
            mset_status('Linking lmdb ' + dset)
            remake_symlink(lmdb_filename, exec_lmdb_filename)
    # Dataset only? Then finish here.
    if not model.get('dataset_only', False):
        # Prepare folder for processing
        mset_status('Preparing model output')
        os.chdir(exec_path)
        for fnbase in 'alexnet', 'alexnetfcn', 'solver_alexnetftc', 'solver_alexnetscratch':
            fn = fnbase + '.prototxt'
            os.symlink(os.path.join(exec_base_path, fn), os.path.join(exec_path, fn))
        cnn_output_path = os.path.join(exec_path, 'out')
        real_cnn_output_path = os.path.join(output_path, 'cnn')
        if not os.path.isdir(real_cnn_output_path):
            os.makedirs(real_cnn_output_path)
        remake_symlink(real_cnn_output_path, cnn_output_path)
        print(exec_path)
        print(real_cnn_output_path)
        # Run caffe!
        mset_status('Run trainer')      
        #Make sure this path matches the location of your caffe.exe
        if(platform.system()=='Linux'):
             caffe_cmd = os.path.join(config.get_caffe_path(),'caffe')
        else: 
             caffe_cmd = os.path.join(config.get_caffe_path(), 'tools','Release', 'caffe.exe')
        cmd = caffe_cmd + ' train' + ' --solver' + ' solver_alexnetftc.prototxt'+ ' --weights ' + config.get_caffe_train_baseweights() + " " + config.caffe_train_options 
        #cmd = [caffe_cmd, 'train', '--solver', 'solver_alexnetftc.prototxt', '--weights', config.get_caffe_train_baseweights()] + config.caffe_train_options.split(' ')
        #cmd = caffe_cmd + ' train' + ' --solver' + ' solver_alexnetftc.prototxt'+ ' --weights ' + config.get_caffe_train_baseweights() + "/bvlc_reference_caffenet.caffemodel " + config.caffe_train_options 
        try:
           print(cmd)
           subprocess.check_output(cmd, shell=True)
        except subprocess.CalledProcessError as e:
           raise RuntimeError("Error calling caffe")
           #More detailed error
           #raise RuntimeError("command '{}' return with error (code {}): {}".format(e.cmd, e.returncode, e.output))
        # Convert to fully convolutional network
        mset_status('Convert to fully convolutional')
        convert_epi1(model_id)
        # Mark as done
        mset_status('Set status')
        db.set_model_status(model_id, db.model_status_trained)
    else:
        mset_status('Set status')
        db.set_model_status(model_id, db.model_status_dataset)
    mset_status('Finished')
    # Set as primary model
    if model.get('primary'):
        db.set_primary_model(model_id)


def run_daemon():
    set_status('Daemon startup...')
    try:
        while True:
            scheduled_models = list(db.get_models(details=False, status=db.model_status_scheduled))
            if len(scheduled_models):
                model = scheduled_models[0]
                set_status('Train model %s (%s)...' % (model['name'], str(model['_id'])))
                cmdline = list(sys.argv) + ['--model-id', str(model['_id'])]
                exec_path, exec_base_path = get_exec_path(model['_id'])
                log_filename = os.path.join(exec_path, 'train.log')
                cmdline = sys.executable +" " + subprocess.list2cmdline(cmdline) + ' >' + log_filename + ' 2>&1'
                print(('Exec:', cmdline))
                rval = subprocess.call(cmdline, shell=True)
                if rval:
                    print ('ERRORED!')
                    print((open(log_filename, 'rt').read()))
            set_status('Waiting for scheduled models...')
            time.sleep(1)
    finally:
        set_status('offline')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Epidermal worker process: Train network from annotated images.')
    parser.add_argument('--model-id', type=str, help='Database entry of model to train. If not specified, start as a '
                                                     'daemon that watches the DB and creates child processes to train '
                                                     'as needed.')

    args = parser.parse_args()
    if args.model_id is None:
        run_daemon()
    else:
        try:
            train(ObjectId(args.model_id))
        except:
            db.set_model_status(ObjectId(args.model_id), db.model_status_failed)
            raise
