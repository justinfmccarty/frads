#!/usr/bin/env python
"""
This module contains functionalities relating to generating matrices for
non-coplanar shading systems
"""

from dataclasses import dataclass
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess as sp
import tempfile as tf
from typing import List
from typing import Optional
from typing import Sequence

from frads import matrix
from frads import geom
from frads import parsers
from frads.types import Primitive
from frads import utils

logger = logging.getLogger("frads.mfacade")


@dataclass
class Model:
    windows: Sequence[Primitive]
    ports: Sequence[Primitive]
    env: Sequence[Path]
    sbasis: str
    rbasis: str


def ncp_compute_back(model: Model, src_dict: dict, opt: str, refl=False):
    """compute front side calculation(backwards)."""
    logger.info("Computing for front side")
    for idx, wp in enumerate(model.windows):
        logger.info(f"Front transmission for window {idx}")
        front_rcvr = matrix.surface_as_receiver(
            prim_list=model.ports,
            basis=model.rbasis,
            left=True,
            offset=None,
            source="glow",
            out=src_dict[f"tb{idx}"],
        )
        # sndr_prim = utils.polygon2prim(wplg, 'fsender', f'window{idx}')
        sndr = matrix.surface_as_sender(
            prim_list=[wp], basis=model.sbasis, left=True, offset=None
        )
        if refl:
            logger.info(f"Front reflection for window {idx}")
            wflip = parsers.parse_polygon(wp.real_arg).flip()
            wflip_prim = utils.polygon2prim(wflip, "breceiver", f"window{idx}")
            back_rcvr = matrix.surface_as_receiver(
                prim_list=[wflip_prim],
                basis="-" + model.rbasis,
                left=False,
                offset=None,
                source="glow",
                out=src_dict[f"rb{idx}"],
            )
            front_rcvr += back_rcvr
        matrix.rfluxmtx(
            sender=sndr, receiver=front_rcvr, env=model.env, out=None, opt=opt
        )


def ncp_compute_front(model: Model, src_dict, opt, refl=False):
    """compute back side calculation."""
    sndr_prim = []
    for p in model.ports:
        np = Primitive(
            p.modifier,
            p.ptype,
            p.identifier,
            p.str_arg,
            parsers.parse_polygon(p.real_arg).flip().to_real(),
        )
        sndr_prim.append(np)
    sndr = matrix.surface_as_sender(
        prim_list=sndr_prim, basis="-" + model.rbasis, offset=None, left=False
    )
    logger.info("Computing for back side")
    for idx, wp in enumerate(model.windows):
        logger.info(f"Back transmission for window {idx}")
        wplg = parsers.parse_polygon(wp.real_arg).flip()
        rcvr_prim = utils.polygon2prim(wplg, "breceiver", f"window{idx}")
        rcvr = matrix.surface_as_receiver(
            prim_list=[rcvr_prim],
            basis="-" + model.sbasis,
            left=False,
            offset=None,
            source="glow",
            out=src_dict[f"tf{idx}"],
        )
        if refl:
            logger.info(f"Back reflection for window {idx}")
            # brcvr_prim = [utils.polygon2prim(plg, "freceiver", "window" + str(i)) for i, pp in enumerate(model.ports)]
            brcvr = matrix.surface_as_receiver(
                prim_list=model.ports,
                basis=model.rbasis,
                left=False,
                offset=None,
                source="glow",
                out=src_dict[f"rf{idx}"],
            )
            rcvr += brcvr
        matrix.rfluxmtx(sender=sndr, receiver=rcvr, env=model.env, out=None, opt=opt)


def klems_wrap(model, src_dict, fwrap_dict, out):
    """prepare wrapping for Klems basis."""
    for key in src_dict:
        for _, _ in enumerate(model.windows):
            inp = src_dict[key]
            rcmd = ["rmtxop", "-fa", "-t", "-c", ".265", ".67", ".065", inp]
            ps1 = sp.run(rcmd, stdout=sp.PIPE)
            with open(fwrap_dict[key], "wb") as wtr:
                sp.run(["getinfo", "-"], input=ps1.stdout, stdout=wtr)
    for i, _ in enumerate(model.window):
        out_name = out.parent / (out.stem + f"{i}.xml")
        sub_dict = {k: fwrap_dict[k] for k in fwrap_dict if k.endswith(str(i))}
        cmd = ["wrapBSDF", "-a", model.rbasis, "-c"]
        cmd.extend([" ".join(("-" + i[:2], j)) for i, j in sub_dict.items()])
        with open(out_name, "wb") as wtr:
            sp.run(cmd, stdout=wtr)


def klems_wrap2(out, out2, inp, basis):
    """prepare wrapping for Klems basis."""
    cmd = f"rmtxop -fa -t -c .265 .67 .065 {inp} | getinfo - > {out}"
    sp.run(cmd, shell=True)
    basis_dict = {"kq": "Klems Quarter", "kh": "Klems Half", "kf": "Klems Full"}
    coeff = utils.angle_basis_coeff(basis_dict[basis])
    with open(out, "r") as rdr:
        rows = [map(float, l.split()) for l in rdr.readlines()]
    res = [[str(val / c) for val in row] for row, c in zip(rows, coeff)]
    with open(out2, "w") as wtr:
        [wtr.write("\t".join(row) + "\n") for row in res]


def rttree_reduce(ttrank, ttlog2, pctcull, refl, src, dest, spec="Visible"):
    """call rttree_reduce to reduce shirley-chiu to tensor tree.
    translated from genBSDF.pl.
    """
    CIEuv = "Xi=.5141*Ri+.3239*Gi+.1620*Bi;Yi=.2651*Ri+.6701*Gi+.0648*Bi;Zi=.0241*Ri+.1229*Gi+.8530*Bi;den=Xi+15*Yi+3*Zi;uprime=if(Yi,4*Xi/den,4/19);vprime=if(Yi,9*Yi/den,9/19);"

    ns2 = int((2**ttlog2) ** 2)
    if spec == "Visible":
        cmd = [
            "rcalc",
            "-e",
            f"Omega:PI/{ns2}",
            "-e",
            "Ri=$1;Gi=$2;Bi=$3",
            "-e",
            CIEuv,
            "-e",
            "$1=Yi/Omega",
        ]
    elif spec == "CIE-u":
        cmd = ["rcalc", "-e", "Ri=$1;Gi=$2;Bi=$3", "-e", CIEuv, "-e", "$1=uprime"]
    elif spec == "CIE-v":
        cmd = ["rcalc", "-e", "Ri=$1;Gi=$2;Bi=$3", "-e", CIEuv, "-e", "$1=vprime"]

    if os.name == "posix":
        cmd.insert(1, "-if3")
    if pctcull >= 0:
        avg = "-a" if refl else ""
        pcull = pctcull if spec == "Visible" else (100 - (100 - pctcull) * 0.25)
        rtcmd = [
            "rttree_reduce",
            avg,
            "-h",
            "-ff",
            "-t",
            pcull,
            "-r",
            ttrank,
            "-g",
            ttlog2,
        ]
        if os.name == "posix":
            cmd.extend(["-of", src])
            ps1 = sp.run(cmd + ["-of", src], stdout=sp.PIPE)
            with open(dest, "wb") as wtr:
                ps2 = sp.run(rtcmd, input=ps1.stdout, stdout=wtr)
        else:
            ps1 = sp.run(["rcollate", "-ho", "-oc", "1", src], stdout=sp.PIPE)
            ps2 = sp.run(cmd, input=ps1.stdout, stdout=sp.PIPE)
            with open(dest, "wb") as wtr:
                sp.run(rtcmd, input=ps2.stdout, stdout=wtr)
    else:
        if os.name == "posix":
            with open(dest, "wb") as wtr:
                sp.run(cmd.append(src), stdout=wtr)
        else:
            ps1 = sp.run(["rcollate", "-ho", "-oc", "1", src], stdout=sp.PIPE)
            with open(dest, "wb") as wtr:
                sp.run(cmd, input=ps1.stdout, stdout=wtr)


def tt_wrap(model, src_dict, fwrap_dict, out, refl) -> None:
    """call wrapBSDF to wrap a XML file."""
    sc = int(model.rbasis[2:])
    ttlog2 = math.log(sc, 2)
    assert ttlog2 % int(ttlog2) == 0
    ttrank = 4  # only anisotropic
    pctcull = 90
    ttlog2 = int(ttlog2)
    for i, _ in enumerate(model.windows):
        sub_key = [k for k in src_dict if k.endswith(str(i))]
        sub_dict = {k: fwrap_dict[k] for k in sub_key}
        for key in sub_key:
            rttree_reduce(ttrank, ttlog2, pctcull, refl, src_dict[key], fwrap_dict[key])
        cmd = ["wrapBSDF", "-a", "t4", "-s", "Visible"]
        cmd += [" ".join(("-" + i[:2], j)) for i, j in sub_dict.items()]
        cmd += f"> {out}.xml"
        with open(out, "wb") as wtr:
            sp.run(cmd, stdout=wtr)


def gen_ncp_mtx(
    model: Model,
    out: Path,
    opt: Optional[str] = None,
    refl=False,
    forw=False,
    wrap=True,
    solar=False,
) -> None:

    # Collect all the primitives
    all_prims = []
    for path in model.env:
        all_prims.extend(utils.unpack_primitives(path))

    # Find out the modifier of the ncp polygon
    ncp_mod = [prim.modifier for prim in ncp_prims if prim.ptype == "polygon"][0]

    # Find out the ncp material primitive
    ncp_mat: Primitive
    ncp_type: str = ""
    for prim in all_prims:
        if prim.identifier == ncp_mod:
            ncp_mat = prim
            ncp_type = prim.ptype
            break
    if ncp_type == "":
        raise ValueError("Unknown NCP material")

    dirname = out.parent
    if solar and ncp_type == "BSDF":
        logger.info("Computing for solar and visible spectrum...")
        xmlpath = ncp_mat.str_arg.split()[2]
        td = tf.mkdtemp()
        with open(xmlpath) as rdr:
            raw = rdr.read()
        raw = raw.replace(
            '<Wavelength unit="Integral">Visible</Wavelength>',
            '<Wavelength unit="Integral">Visible2</Wavelength>',
        )
        raw = raw.replace(
            '<Wavelength unit="Integral">Solar</Wavelength>',
            '<Wavelength unit="Integral">Visible</Wavelength>',
        )
        raw = raw.replace(
            '<Wavelength unit="Integral">Visible2</Wavelength>',
            '<Wavelength unit="Integral">Solar</Wavelength>',
        )
        solar_xml_path = os.path.join(td, "solar.xml")
        with open(solar_xml_path, "w") as wtr:
            wtr.write(raw)
        _strarg = ncp_mat.str_arg.split()
        _strarg[2] = solar_xml_path
        solar_ncp_mat = Primitive(
            ncp_mat.modifier,
            ncp_mat.ptype,
            ncp_mat.identifier + ".solar",
            " ".join(_strarg),
            "0",
        )

        _env_path = os.path.join(td, "env_solar.rad")
        with open(_env_path, "w") as wtr:
            for prim in all_prims:
                wtr.write(str(prim))
        outsolar = dirname / ("_solar_{out.stem}.dat")

    klems = True
    if wrap and (model.rbasis.startswith("sc")) and (model.sbasis.startswith("sc")):
        klems = False
        sc = int(model.rbasis[2:])
        ttlog2 = math.log(sc, 2)
        if ttlog2 % int(ttlog2) != 0:
            raise ValueError("Invalid tensor tree resolution.")
        if opt is not None:
            opt += " -hd -ff"
        else:
            opt = "-hd -ff"
    with tf.TemporaryDirectory() as td:
        src_dict = {}
        fwrap_dict = {}
        for idx, _ in enumerate(model.windows):
            _tf = f"tf{idx}"
            _rf = f"rf{idx}"
            _tb = f"tb{idx}"
            _rb = f"rb{idx}"
            src_dict[_tb] = Path(td, _tb + ".dat")
            fwrap_dict[_tb] = Path(td, _tb + "p.dat")
            if forw:
                src_dict[_tf] = Path(td, _tf + ".dat")
                fwrap_dict[_tf] = Path(td, _tf + "p.dat")
            if refl:
                src_dict[_rb] = Path(td, _rb + ".dat")
                fwrap_dict[_rb] = Path(td, _rb + "p.dat")
                if forw:
                    src_dict[_rf] = Path(td, _rf + ".dat")
                    fwrap_dict[_rf] = Path(td, _rf + "p.dat")
        ncp_compute_back(model, src_dict, opt, refl=refl)
        if forw:
            ncp_compute_front(model, src_dict, opt, refl=refl)
        if wrap:
            if klems:
                klems_wrap(model, src_dict, fwrap_dict, out)
            else:
                tt_wrap(model, src_dict, fwrap_dict, out, refl)
        else:
            for key, file in src_dict.items():
                out_name = f"{out.stem}_{key}.mtx"
                file.rename(out.parent / out_name)

    if solar and ncp_type == "BSDF":
        # process_thread.join()
        vis_dict = {}
        sol_dict = {}
        oname = Path(args["o"]).stem
        mtxs = [
            os.path.join(dirname, mtx)
            for mtx in os.listdir(dirname)
            if mtx.endswith(".mtx")
        ]
        for mtx in mtxs:
            _direc = Path(mtx).stem.split("_")[-1][:2]
            mtxname = Path(mtx).stem
            if mtxname.startswith(oname):
                # vis_dict[_direc] = os.path.join(dirname, f"_vis_{_direc}")
                vis_dict[_direc] = os.path.join(td, f"vis_{_direc}")
                out2 = os.path.join(dirname, f"vis_{_direc}")
                klems_wrap(vis_dict[_direc], out2, mtx, args.ss)
            if mtxname.startswith("_solar_"):
                sol_dict[_direc] = os.path.join(td, f"sol_{_direc}")
                out2 = os.path.join(dirname, f"sol_{_direc}")
                klems_wrap(sol_dict[_direc], out2, mtx, args.ss)
        cmd = f"wrapBSDF -a {args.ss} -c -s Visible "
        cmd += " ".join([f"-{key} {vis_dict[key]}" for key in vis_dict])
        cmd += " -s Solar "
        cmd += " ".join([f"-{key} {sol_dict[key]}" for key in sol_dict])
        cmd += f" > {os.path.join(dirname, oname)}.xml"
        os.system(cmd)
        shutil.rmtree(td)
        [os.remove(mtx) for mtx in mtxs]


# class Genfmtx(object):
#     """Generate facade matrix."""
#
#     def __init__(self, *, win_polygons, port_prim, out, env, sbasis, rbasis, opt, refl, forw, wrap):
#         """Generate the appropriate aperture for F matrix generation.
#         Parameters:
#             win_prim: window geometry primitive;
#             ncs_prim: shading geometry primitive;
#             out_path: output file path
#             depth: distance between the window and the farther point of the shade
#             geometry;
#             scale: scale factor to window geometry so that it encapsulate the
#             projected shadeing geometry onto the window surface;
#             FN: Bool, whether to generate four aperture separately
#             rs: receiver sampling basis;
#             ss: sender sampling basis;
#             refl: Do reflection?;
#             forw: Do forward tracing calculation?;
#             wrap: wrap to .XML file?;
#             **kwargs: other arguments that will be passed to Genmtx;
#         """
#         self.win_polygon = win_polygons
#         self.port_prim = port_prim
#
#         self.out = out
#         self.outdir: str = os.path.dirname(out)
#         self.out_name = util.basename(out)
#         self.env = env
#
#         self.rbasis = rbasis
#         self.sbasis = sbasis
#         self.opt = opt
#         self.refl = refl
#         if wrap == True and rbasis.startswith('sc') and sbasis.startswith('sc'):
#             sc = int(rbasis[2:])
#             ttlog2 = math.log(sc, 2)
#             assert ttlog2 % int(ttlog2) == 0
#             self.ttrank = 4  # only anisotropic
#             self.pctcull = 90
#             self.ttlog2 = int(ttlog2)
#             self.opt += ' -hd -ff'
#         self.td = tf.mkdtemp()
#         src_dict = {}
#         fwrap_dict = {}
#         for idx in range(len(self.win_polygon)):
#             _tf = f'tf{idx}'
#             _rf = f'rf{idx}'
#             _tb = f'tb{idx}'
#             _rb = f'rb{idx}'
#             src_dict[_tb] = os.path.join(self.td, f'{_tb}.dat')
#             fwrap_dict[_tb] = os.path.join(self.td, f'{_tb}p.dat')
#             if forw:
#                 src_dict[_tf] = os.path.join(self.td, f'{_tf}.dat')
#                 fwrap_dict[_tf] = os.path.join(self.td, f'{_tf}p.dat')
#             if refl:
#                 src_dict[_rb] = os.path.join(self.td, f'{_rb}.dat')
#                 fwrap_dict[_rb] = os.path.join(self.td, f'{_rb}p.dat')
#                 if forw:
#                     src_dict[_rf] = os.path.join(self.td, f'{_rf}.dat')
#                     fwrap_dict[_rf] = os.path.join(self.td, f'{_rf}p.dat')
#         self.compute_back(src_dict)
#         if forw:
#             self.compute_front(src_dict)
#         self.src_dict = src_dict
#         self.fwrap_dict = fwrap_dict
#         if wrap:
#             self.wrap()
#         else:
#             for key in src_dict:
#                 out_name = f"{self.out_name}_{key}.mtx"
#                 shutil.move(src_dict[key], os.path.join(self.outdir, out_name))
#         shutil.rmtree(self.td, ignore_errors=True)
#
#     def compute_back(self, src_dict):
#         """compute front side calculation(backwards)."""
#         logger.info('Computing for front side')
#         for idx, win_polygon in enumerate(self.win_polygon):
#             logger.info(f'Front transmission for window {idx}')
#             front_rcvr = rm.Receiver.as_surface(
#                 prim_list=self.port_prim, basis=self.rbasis,
#                 left=True, offset=None, source='glow', out=src_dict[f'tb{idx}'])
#             sndr_prim = radutil.polygon2prim(win_polygon, 'fsender', f'window{idx}')
#             sndr = rm.Sender.as_surface(
#                 prim_list=[sndr_prim], basis=self.sbasis, left=True, offset=None)
#             if self.refl:
#                 logger.info(f'Front reflection for window {idx}')
#                 back_window = win_polygon.flip()
#                 back_window_prim = radutil.polygon2prim(
#                     back_window, 'breceiver', f'window{idx}')
#                 back_rcvr = rm.Receiver.as_surface(
#                     prim_list=[back_window_prim], basis="-"+self.rbasis,
#                     left=False, offset=None, source='glow', out=src_dict[f'rb{idx}'])
#                 front_rcvr += back_rcvr
#             rm.rfluxmtx(sender=sndr, receiver=front_rcvr, env=self.env,
#                         out=None, opt=self.opt)
#
#     def compute_front(self, src_dict):
#         """compute back side calculation."""
#         sndr_prim = []
#         for p in self.port_prim:
#             np = p.copy()
#             np['real_args'] = np['polygon'].flip().to_real()
#             sndr_prim.append(np)
#         sndr = rm.Sender.as_surface(
#             prim_list=sndr_prim, basis="-"+self.rbasis, offset=None, left=False)
#         logger.info('Computing for back side')
#         for idx in range(len(self.win_polygon)):
#             logger.info(f'Back transmission for window {idx}')
#             win_polygon = self.win_polygon[idx].flip()
#             rcvr_prim = radutil.polygon2prim(win_polygon, 'breceiver', f'window{idx}')
#             rcvr = rm.Receiver.as_surface(
#                 prim_list=[rcvr_prim], basis="-"+self.sbasis,
#                 left=False, offset=None, source='glow', out=src_dict[f'tf{idx}'])
#             if self.refl:
#                 logger.info(f'Back reflection for window {idx}')
#                 brcvr_prim = [
#                     radutil.polygon2prim(self.port_prim[i]['polygon'], 'freceiver', 'window' + str(i))
#                     for i in range(len(self.port_prim))]
#                 brcvr = rm.Receiver.as_surface(
#                     prim_list=brcvr_prim, basis=self.rbasis,
#                     left=False, offset=None, source='glow',
#                     out=src_dict[f'rf{idx}'])
#                 rcvr += brcvr
#             rm.rfluxmtx(sender=sndr, receiver=rcvr, env=self.env, out=None,
#                        opt=self.opt)
#
#     def klems_wrap(self):
#         """prepare wrapping for Klems basis."""
#         for key in self.src_dict:
#             for _ in range(len(self.win_polygon)):
#                 inp = self.src_dict[key]
#                 out = self.fwrap_dict[key]
#                 cmd = f"rmtxop -fa -t -c .265 .67 .065 {inp} | getinfo - > {out}"
#                 sp.call(cmd, shell=True)
#
#     def wrap(self, **kwargs):
#         """call wrapBSDF to wrap a XML file."""
#         if self.sbasis.startswith('sc') and self.rbasis.startswith('sc'):
#             for i in range(len(self.win_polygon)):
#                 sub_key = [k for k in self.src_dict if k.endswith(str(i))]
#                 sub_dict = {k: self.fwrap_dict[k] for k in sub_key}
#                 for key in sub_key:
#                     self.rttree_reduce(self.src_dict[key], self.fwrap_dict[key])
#                 cmd = 'wrapBSDF -a t4 -s Visible {} '.format(' '.join(
#                     [" ".join(('-' + i[:2], j)) for i, j in sub_dict.items()]))
#                 cmd += f"> {self.out_name}.xml"
#                 sp.call(cmd, shell=True)
#         else:
#             self.klems_wrap()
#             for i, _ in enumerate(self.win_polygon):
#                 out_name = os.path.join(self.outdir, f"{self.out_name}_{i}.xml")
#                 sub_dict = {
#                     k: self.fwrap_dict[k]
#                     for k in self.fwrap_dict if k.endswith(str(i))
#                 }
#                 cmd = 'wrapBSDF -a {} -c {} '.format(self.rbasis, ' '.join(
#                     [" ".join(('-' + i[:2], j)) for i, j in sub_dict.items()]))
#                 cmd += f'> {out_name}'
#                 sp.call(cmd, shell=True)
#
#     def rttree_reduce(self, src, dest, spec='Visible'):
#         """call rttree_reduce to reduce shirley-chiu to tensor tree.
#         copied from genBSDF."""
#         CIEuv = 'Xi=.5141*Ri+.3239*Gi+.1620*Bi;'
#         CIEuv += 'Yi=.2651*Ri+.6701*Gi+.0648*Bi;'
#         CIEuv += 'Zi=.0241*Ri+.1229*Gi+.8530*Bi;'
#         CIEuv += 'den=Xi+15*Yi+3*Zi;'
#         CIEuv += 'uprime=if(Yi,4*Xi/den,4/19);'
#         CIEuv += 'vprime=if(Yi,9*Yi/den,9/19);'
#
#         ns2 = int((2**self.ttlog2)**2)
#         if spec == 'Visible':
#             cmd = f'rcalc -e "Omega:PI/{ns2}" '
#             cmd += '-e "Ri=$1;Gi=$2;Bi=$3" '
#             cmd += f'-e "{CIEuv}" -e "$1=Yi/Omega" '
#         elif spec == 'CIE-u':
#             cmd = 'rcalc -e "Ri=$1;Gi=$2;Bi=$3" '
#             cmd += f'-e "{CIEuv}" -e "$1=uprime"'
#         elif spec == 'CIE-v':
#             cmd = 'rcalc -e "Ri=$1;Gi=$2;Bi=$3" '
#             cmd += f'-e "{CIEuv}" -e "$1=vprime"'
#
#         if os.name == 'posix':
#             cmd = cmd[:5] + ' -if3' + cmd[5:]
#             cmd = cmd.replace('"', "'")
#         if self.pctcull >= 0:
#             avg = "-a" if self.refl else ""
#             pcull = self.pctcull if spec == 'Visible' else (
#                 100 - (100 - self.pctcull) * .25)
#             cmd2 = f"rcollate -ho -oc 1 {src} | "
#             cmd2 += cmd
#             if os.name == 'posix':
#                 cmd2 = cmd + f" -of {src} "
#             cmd2 += f"| rttree_reduce {avg} -h -ff -t {pcull} -r {self.ttrank} -g {self.ttlog2} "
#             cmd2 += f"> {dest}"
#         else:
#             if os.name == 'posix':
#                 cmd2 = cmd + f" {src}"
#             else:
#                 cmd2 = f"rcollate -ho -oc 1 {src} | " + cmd
#         logger.info(cmd2)
#         sp.call(cmd2, shell=True)


def gen_port_prims_from_window_ncp(
    wprim: Sequence[Primitive], nprim: Sequence[Primitive]
) -> List[Primitive]:
    """Generate port primitives from window and non-coplanar shading primitives."""
    if len(wprim) > 1:
        awprim = merge_windows(wprim)
    else:
        awprim = wprim[0]
    wplg = parsers.parse_polygon(awprim.real_arg)
    nplgs = [parsers.parse_polygon(p.real_arg) for p in nprim if p.ptype == "polygon"]
    all_ports = gen_ports_from_window_ncp(wplg, nplgs)
    port_prims = []
    for idx, plg in enumerate(all_ports):
        new_prim = utils.polygon2prim(plg, "port", f"portf{idx+1}")
        logger.debug(str(new_prim))
        port_prims.append(new_prim)
    return port_prims


def gen_port_prims_from_window(
    wprim: Sequence[Primitive], depth: float, scale_factor: float
) -> List[Primitive]:
    """Generate port primitives from window primitives, depth, and scale factor."""
    if len(wprim) > 1:
        awprim = merge_windows(wprim)
    else:
        awprim = wprim[0]
    wpoly = parsers.parse_polygon(awprim.real_arg)
    extrude_vector = wpoly.normal().reverse().scale(depth)
    scale_vector = geom.Vector(scale_factor, scale_factor, scale_factor)
    scaled_window = wpoly.scale(scale_vector, wpoly.centroid())
    all_ports = scaled_window.extrude(extrude_vector)[1:]
    port_prims = []
    for idx, plg in enumerate(all_ports):
        new_prim = utils.polygon2prim(plg, "port", f"portf{idx+1}")
        logger.debug(str(new_prim))
        port_prims.append(new_prim)
    return port_prims


def gen_ports_from_window_ncp(
    wp: geom.Polygon, np: Sequence[geom.Polygon]
) -> List[geom.Polygon]:
    """
    Generate ports polygons that encapsulate the window and NCP geometries.

    window and NCP geometries are rotated around +Z axis until
    the axis-aligned bounding box projected onto XY plane is the smallest, thus the systems are facing
    orthogonal direction. A boundary box is then generated with a slight
    outward offset. This boundary box is then rotated back the same amount
    to encapsulate the original window and NCP geomteries.
    """
    wn = wp.normal()
    if (abs(wn.y) == 1) or (abs(wn.x) == 1):
        np.append(wp)
        bbox = geom.getbbox(np, offset=0.00)
        bbox.remove([b for b in bbox if b.normal().reverse() == wn][0])
        return [b.move(wn.scale(-0.1)) for b in bbox]
    xax = [1, 0, 0]
    _xax = [-1, 0, 0]
    yax = [0, 1, 0]
    _yax = [0, -1, 0]
    zaxis = geom.Vector(0, 0, 1)
    rm_pg = [xax, _yax, _xax, yax]
    area_list = []
    win_normals = []
    # Find axiel aligned rotation angle
    bboxes = []
    for deg in range(90):
        rad = math.radians(deg)
        win_polygon_r = wp.rotate(zaxis, rad)
        win_normals.append(win_polygon_r.normal())
        ncs_polygon_r = [p.rotate(zaxis, rad) for p in np]
        ncs_polygon_r.append(win_polygon_r)
        _bbox = geom.getbbox(ncs_polygon_r, offset=0.001)
        bboxes.append(_bbox)
        area_list.append(_bbox[0].area())
    # Rotate to position
    deg = area_list.index(min(area_list))
    rrad = math.radians(deg)
    bbox = bboxes[deg]
    _win_normal = [round(i, 1) for i in win_normals[deg].to_list()]
    del bbox[rm_pg.index(_win_normal) + 2]
    rotate_back = [pg.rotate(zaxis, rrad * -1) for pg in bbox]
    return rotate_back


def merge_windows(prims: Sequence[Primitive]) -> Primitive:
    """Merge rectangles if coplanar."""
    polygons = [parsers.parse_polygon(p.real_arg) for p in prims]
    normals = [p.normal() for p in polygons]
    if len(set(normals)) > 1:
        raise ValueError("Windows not co-planar")
    points = [i for p in polygons for i in p.vertices]
    hull_polygon = geom.convexhull(points, normals[0])
    modifier = prims[0].modifier
    identifier = prims[0].identifier
    new_prim = utils.polygon2prim(hull_polygon, modifier, identifier)
    return new_prim
