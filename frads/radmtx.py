#!/usr/bin/env python3
"""
Generate matrices.

Scenarios:
1. polygon to polygon (e.g facade matrix)
2. polygon to sky (e.g. daylight matrix)
3. view to polygon (e.g. image based view matrix)
4. grid to polygon (e.g. point based view matrix)
5. view to suns (e.g 5PM direct sun coefficient)
6. grid to suns

T.Wang

"""

import argparse
from frads import makesky
from frads import radgeom
import os
import subprocess as sp
import tempfile as tf
from frads import radutil
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler('radmtx.log')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


class Sender(object):
    """Sender object for matrix generation."""

    def __init__(self, *, form, path, sender, basis, offset, xres, yres, c2c, linecnt):
        """Instantiate the instance.

        Parameters:
            sender: path to view file/ pts grid/ other surface file;
            sampling: sender sampling basis, required when sender is a surface;
            offset: move the sender surface in its normal direction
            xres, yres: xy resolution of the image, required if sender is a viewfile;
            c2c (bool): Set to True to trim the rays that are sent to the corner of
            the image by setting the ray direction to 0 0 0;
        """
        self.form = form
        self.sender = sender
        self.path = path
        self.offset = offset
        self.basis = basis
        self.xres = xres
        self.yres = yres
        self.c2c = c2c
        self.linecnt = linecnt
        logger.info(f"Sender: {sender}")

    @classmethod
    def as_surface(cls, *, prim_list, basis, offset):
        prim_str = prepare_surface(prim_list, basis, offset=offset)
        fd, path = tf.mkstemp(prefix='sndr_srf')
        with open(path, 'w') as wtr:
            wtr.write(prim_str)
        return cls(formt='srf', path=path, sender=prim_str, basis=basis, offset=offset,
            xres=None, yres=None, c2c=None, linecnt=None)

    @classmethod
    def as_view(cls, *, vu_dict, ray_cnt, xres, yres, c2c):
        assert None not in (xres, yres), "Need to specify resolution"
        vcmd = f"vwrays {radutil.opt2str(vu_dict)} -x {xres} -y {yres} -d"
        res_eval = sp.run(vcmd, shell=True, check=True, stdout=sp.PIPE).stdout.decode().split()
        xres = res_eval[1]
        yres = res_eval[3]
        cmd = f"vwrays -ff -x {xres} -y {yres} "
        if ray_cnt > 1:
            vu_dict['c'] = ray_cnt
            vu_dict['pj'] = 0.7 # placeholder
        logger.info(f"Ray count is {ray_cnt}")
        vu_str = radutil.opt2str(vu_dict)
        cmd += vu_str
        if vu_dict['vt'] == 'a' and c2c:
            cmd += Sender.crop2circle(ray_cnt, xres)
        fd, path = tf.mkstemp(prefix='vufile')
        cmd += f"> {path}"
        logger.info(cmd + "\n")
        sp.run(cmd, shell=True)
        return cls(form='vu', path=path, sender=vu_dict, basis=None, offset=None, xres=xres,
                   yres=yres, c2c=c2c, linecnt=None)

    @classmethod
    def as_pts(cls, pts_list):
        grid_str = os.linesep.join([' '.join(map(str, l)) for l in pts_list]) + os.linesep
        fd, path = tf.mkstemp(prefix='sndr_grid')
        with open(path, 'w') as wtr:
            wtr.write(grid_str)
        return cls(form='pts', path=path, sender=grid_str, basis=None, offset=None, xres=None,
                   yres=None, c2c=None, linecnt=len(pts_list))

    @staticmethod
    def crop2circle(ray_cnt, xres):
        cmd = "| rcalc -if6 -of "
        cmd += f'-e "DIM:{xres};CNT:{ray_cnt}" '
        cmd += '-e "pn=(recno-1)/CNT+.5" '
        cmd += '-e "frac(x):x-floor(x)" -e "xpos=frac(pn/DIM);ypos=pn/(DIM*DIM)"'
        cmd += ' -e "incir=if(.25-(xpos-.5)*(xpos-.5)-(ypos-.5)*(ypos-.5),1,0)"'
        cmd += ' -e "$1=$1;$2=$2;$3=$3;$4=$4*incir;$5=$5*incir;$6=$6*incir"'
        if os.name == "posix":
            cmd = cmd.replace('"', "'")
        return cmd

    def remove(self):
        os.remove(self.path)


class Receiver(object):
    """Receiver object for matrix generation."""

    def __init__(self, *, path, receiver, basis, modifier):
        """Instantiate the receiver object.

        Parameters:
            receiver (str): filepath {sky | sun | file_path}
            basis: receiver sampling basis {kf | r1 | sc25...}
        """
        self.receiver = receiver
        self.path = path
        self.basis = basis
        self.modifier = modifier

    def __add__(self, other):
        self.receiver += other.receiver
        return self

    @classmethod
    def as_sun(cls, *, basis, smx_path, window_paths):
        gensun = makesky.Gensun(int(basis[-1]))
        if (smx_path is None) and (window_paths is None):
            str_repr, mod_lines = gensun.gen_full()
        else:
            str_repr, mod_lines = gensun.gen_cull(smx_path=smx_path,
                                                  window_paths=window_paths)
        fd, path = tf.mkstemp(prefix='sun')
        mfd, modifier = tf.mkstemp(prefix='mod')
        with open(path, 'w') as wtr:
            wtr.write(str_repr)
        with open(modifier, 'w') as wtr:
            wtr.write(mod_lines)
        return cls(path=path, receiver=str_repr, basis=basis, modifier=modifier)

    @classmethod
    def as_sky(cls, basis):
        assert basis.startswith('r'), 'Sky basis need to be Treganza/Reinhart'
        sky_str = makesky.basis_glow(basis)
        logger.info(sky_str)
        fd, path = tf.mkstemp(prefix='sky')
        with open(path, 'w') as wtr:
            wtr.write(sky_str)
        return cls(path=path, receiver=sky_str, basis=basis, modifier=None)

    @classmethod
    def as_surface(cls, *, prim_list, basis, offset, left, source, out):
        rcvr_str = prepare_surface(prim_list, basis, offset=offset,
                                   left=left, source=source, out=out)
        fd, path = tf.mkstemp(prefix='rsrf')
        with open(path, 'w') as wtr:
            wtr.write(rcvr_str)
        return cls(path=path, receiver=rcvr_str, basis=basis, modifier=None)

    def remove(self):
        if self.modifier is not None:
            os.remove(self.modifier)
        os.remove(self.path)


def prepare_surface(*, prims, basis, left, offset, source, out):
    """."""
    assert basis is not None, 'Sampling basis cannot be None'
    upvector = radutil.up_vector(prims)
    basis = "-" + basis if left else basis
    modifier_set = set([p['modifier'] for p in prims])
    if len(modifier_set) != 1:
        logger.warn("Primitives don't share modifier")
    src_mod = f"rflx{prims[0]['modifier']}"
    header = f'#@rfluxmtx h={basis} u={upvector}\n'
    if out is not None:
        header += f"#@rfluxmtx o={out}\n\n"
    if source is not None:
        source_line = f"void {source} {src_mod}\n0\n0\n4 1 1 1 0\n\n"
        header += source_line
    modifiers = [p['modifier'] for p in prims]
    identifiers = [p['identifier'] for p in prims]
    for p in prims:
        if p['identifier'] in modifiers:
            p['identifier'] = 'discarded'
    for p in prims:
        p['modifier'] = src_mod
    content = ''
    if offset is not None:
        for p in prims:
            pg = p['polygon']
            offset_vec = pg.normal().scale(offset)
            moved_pts = [pt + offset_vec for pt in pg.vertices]
            p['real_args'] = radgeom.Polygon(moved_pts).to_real()
            content += radutil.put_primitive(p)
    else:
        for p in prims:
            content += radutil.put_primitive(p)
    return header + content

def gen_mtx(sender, receiver, env, out, opt):
    if sender.form == 'srf':
        cmd = f"rfluxmtx {opt} {sender.path} {receiver.path} {' '.join(env)} > {out}"
    else:
        cmd = f"rfluxmtx < {sender.path} {opt} "
        if sender.form == 'pts':
            if 'c' in opt:
                assert int(opt['c']) == 1, "ray count can't be greater than 1"
            cmd += f"-I+ -faf -y {sender.linecnt} " #force illuminance calc
        elif sender.form == 'vu':
            out = os.path.join(out, '%04d.hdr')
            cmd += f"-ffc -x {sender.xres} -y {sender.yres} -ld- "
        cmd += f"-o {out} - {receiver.path} {' '.join(env)}"
    logger.info(cmd)
    sp.run(cmd, shell=True)
    sender.remove()
    receiver.remove()

def sun_oct(receiver, env):
    """Generate an octree of the environment and the receiver."""
    fd, _env = tf.mkstemp()
    ocmd = 'oconv {} {} > {}'.format(env, receiver.path, _env)
    logger.info(ocmd)
    sp.call(ocmd, shell=True)
    return _env

def sun_mtx(sender, receiver, env, out, opt):
    _env = sun_oct(receiver, env)
    cmd = f'rcontrib < {sender.path} {opt} -fo+ '
    if sender.form == 'pts':
        cmd = f'-faf '
    elif sender.form == 'vu':
        out = os.path.join(out, '%04d.hdr')
        cmd += f"-ffc -x {sender.xres} -y {sender.yres} "
    cmd += f'-o {out} -M {receiver.modifier} {_env}'
    logger.info(cmd)
    sp.call(cmd, shell=True)


if __name__ == '__main__':
    import pdb
    with open('test.vf') as rdr:
        vuline = rdr.read()
    vu_dict = radutil.parse_vu(vuline)
    vu_sndr = Sender.as_view(vu_dict=vu_dict,xres=1000, yres=1000, c2c=True)
    pts_list = [[0,0,0,0,-1,0],[1,2,3,0,0,1]]
    pt_sndr = Sender.as_pts(pts_list)
    with open('test.rad') as rdr:
        rline = rdr.readlines()
    prim_list = radutil.parse_primitive(rline)
    srf_sndr = Sender.as_surface(prim_list=prim_list, basis='kf', offset=None)
    rsky = Receiver.as_sky(basis='r4')
    rsun = Receiver.as_sun(basis='r6', smx_path=None, window_paths=None)
    with open('test.rad') as rdr:
        rline = rdr.readlines()
    prim_list = radutil.parse_primitive(rline)
    rsrf = Receiver.as_surface(prim_list=prim_list, basis='kf', offset=1, left=False,
                        source='glow',out=None)
