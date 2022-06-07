# -*- coding:utf-8 -*-

"""Reader and writer for VRML format

:Organization:
 AIST

Requirements
------------
* numpy
* omniorb-python
* jinja2 template engine

Examples
--------

Read vrml model data given the file path

>>> r = VRMLReader()
>>> m = r.read(os.path.expandvars('$OPENHRP_MODEL_PATH/closed-link-sample.wrl'))

Write simulation model in VRML format

>>> import subprocess
>>> subprocess.call('rosrun xacro xacro.py `rospack find atlas_description`/robots/atlas_v3.urdf.xacro > /tmp/atlas.urdf', shell=True)
0
>>> from . import urdf
>>> r = urdf.URDFReader()
>>> m = r.read('/tmp/atlas.urdf')
>>> w = VRMLWriter()
>>> w.write(m, '/tmp/atlas.wrl')

>>> from . import sdf
>>> r = sdf.SDFReader()
>>> m = r.read('model://pr2/model.sdf')
>>> w = VRMLWriter()
>>> w.write(m, '/tmp/pr2.wrl')
"""

from . import model
from . import utils
import os
import sys
import time
import subprocess
import atexit
import logging
import warnings
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    from .thirdparty import transformations as tf
import math
import numpy
import copy
import jinja2
import uuid
try:
    import CORBA
    import CosNaming
    import OpenHRP
except ImportError:
    print("Unable to find CORBA and OpenHRP library.")
    print("You can install the library by:")
    print("$ sudo add-apt-repository ppa:hrg/daily")
    print("$ sudo apt-get update")
    print("$ sudo apt-get install openhrp openrtm-aist-python")
    pass

plist = []
def terminator():
    global plist
    for p in plist:
        p.terminate()
atexit.register(terminator)

class VRMLReader(object):
    '''
    VRML reader class
    '''
    def __init__(self):
        self._orb = CORBA.ORB_init([sys.argv[0],
                                    "-ORBInitRef",
                                    "NameService=corbaloc::localhost:2809/NameService"],
                                   CORBA.ORB_ID)
        self._loader = None
        self._ns = None
        self._model = None
        self._joints = []
        self._links = []
        self._linknamemap = {}
        self._linknamemap['world'] = 'world'
        self._materials = []
        self._sensors = []
        self._assethandler = None

    def read(self, f, assethandler=None, options=None):
        '''
        Read vrml model data given the file path
        '''
        self._assethandler = assethandler
        try:
            self.resolveModelLoader()
            self._loader.clearData()
        except (CosNaming.NamingContext.NotFound, CORBA.TRANSIENT):
            logging.info("try running openhrp-model-loader")
            plist.append(subprocess.Popen(["openhrp-model-loader"]))
            for t in range(0, 6):
                if t == 5:
                    logging.error("unable to find openhrp-model-loader")
                    raise CosNaming.NamingContext.NotFound
                try:
                    self.resolveModelLoader()
                    self._loader.clearData()
                except (CosNaming.NamingContext.NotFound, CORBA.TRANSIENT):
                    time.sleep(1)
                else:
                    logging.info("resolved openhrp-model-loader")
                    break
        try:
            self._model = self._loader.loadBodyInfo(f)
        except CORBA.TRANSIENT:
            logging.error('unable to connect to model loader corba service (is "openhrp-model-loader" running?)')
            raise
        bm = model.BodyModel()
        bm.name = self._model._get_name()
        self._joints = []
        self._links = []
        self._materials = []
        self._sensors = []
        self._hrplinks = self._model._get_links()
        self._hrpshapes = self._model._get_shapes()
        self._hrpapperances = self._model._get_appearances()
        self._hrpmaterials = self._model._get_materials()
        self._hrptextures = self._model._get_textures()
        self._hrpextrajoints = self._model._get_extraJoints()
        mid = 0
        for a in self._hrpmaterials:
            m = model.MaterialModel()
            m.name = "material-%i" % mid
            mid = mid + 1
            m.ambient = a.ambientIntensity
            m.diffuse = a.diffuseColor + [1.0]
            m.specular = a.specularColor + [1.0]
            m.emission = a.emissiveColor + [1.0]
            m.shininess = a.shininess
            m.transparency = a.transparency
            self._materials.append(m)
        root = self._hrplinks[0]
        bm.trans = numpy.array(root.translation)
        if root.jointType == 'fixed':
            world = model.JointModel()
            world.name = 'world'
            self.readChild(world, root)
        else:
            lm = self.readLink(root)
            self._links.append(lm)
            jm = model.JointModel()
            jm.name = root.name
            for c in root.childIndices:
                self.readChild(jm, self._hrplinks[c])
        for j in self._hrpextrajoints:
            # extra joint for closed link models
            m = model.JointModel()
            m.jointType = model.JointModel.J_REVOLUTE
            m.parent = j.link[0]
            m.child = j.link[1]
            m.name = j.name
            m.axis = model.AxisData()
            m.axis.axis = numpy.array(j.axis)
            m.trans = numpy.array(j.point[1])
            m.offsetPosition = True
            self._joints.append(m)
        bm.links = self._links
        bm.joints = self._joints
        for j in bm.joints:
            j.parent = self._linknamemap[j.parent]
            j.child = self._linknamemap[j.child]
        bm.sensors = self._sensors
        return bm

    def readLink(self, m):
        lm = model.LinkModel()
        if len(m.segments) > 0:
            lm.name = m.segments[0].name
        else:
            lm.name = m.name
        self._linknamemap[m.name] = lm.name
        lm.mass = m.mass
        lm.centerofmass = numpy.array(m.centerOfMass)
        lm.inertia = numpy.array(m.inertia).reshape(3, 3)
        lm.visuals = []
        for s in m.sensors:
            sm = model.SensorModel()
            sm.name = s.name
            sm.parent = lm.name
            sm.trans = numpy.array(s.translation)
            # sensors in OpenHRP is defined based on Z-axis up. so we
            # will rotate them to X-axis up here.
            # see http://www.openrtp.jp/openhrp3/jp/create_model.html
            sm.rot = tf.quaternion_about_axis(s.rotation[3], s.rotation[0:3])
            if s.type == 'Vision':
                sm.rot = tf.quaternion_multiply(sm.rot, tf.quaternion_about_axis(math.pi, [1, 0, 0]))
                sm.sensorType = model.SensorModel.SS_CAMERA
                sm.data = model.CameraData()
                sm.data.near = s.specValues[0]
                sm.data.far = s.specValues[1]
                sm.data.fov = s.specValues[2]
                if s.specValues[3] == 1:
                    sm.data.cameraType = model.CameraData.CS_COLOR
                elif s.specValues[3] == 2:
                    sm.data.cameraType = model.CameraData.CS_MONO
                elif s.specValues[3] == 3:
                    sm.data.cameraType = model.CameraData.CS_DEPTH
                elif s.specValues[3] == 4:
                    sm.data.cameraType = model.CameraData.CS_RGBD
                else:
                    raise Exception('unsupported camera type: %i' % s.specValues[3])
                sm.data.width = s.specValues[4]
                sm.data.height = s.specValues[5]
                sm.rate = s.specValues[6]
            elif s.type == 'Range':
                rot = tf.quaternion_multiply(sm.rot, tf.quaternion_about_axis(-math.pi/2, [0, 0, 1]))
                rot = tf.quaternion_multiply(rot, tf.quaternion_about_axis(math.pi/2, [0, 1, 0]))
                sm.rot = tf.quaternion_multiply(rot, tf.quaternion_about_axis(math.pi, [1, 0, 0]))
                sm.sensorType = model.SensorModel.SS_RAY
                sm.data = model.RayData()
                (scanangle, scanstep, scanrate, maxdistance) = s.specValues
                sm.data.min_angle = - scanangle / 2
                sm.data.max_angle = scanangle / 2
                sm.data.min_range = 0.08
                sm.data.max_range = maxdistance
                sm.rate = scanrate
            self._sensors.append(sm)
        for s in m.shapeIndices:
            sm = model.ShapeModel()
            sm.name = lm.name + "-shape-%i" % s.shapeIndex
            sm.matrix = numpy.matrix(s.transformMatrix+[0, 0, 0, 1]).reshape(4, 4)
            sdata = self._hrpshapes[s.shapeIndex]
            if sdata.primitiveType == OpenHRP.SP_MESH:
                sm.shapeType = model.ShapeModel.SP_MESH
                sm.data = self.readMesh(sdata)
            elif sdata.primitiveType == OpenHRP.SP_SPHERE and numpy.allclose(sm.matrix, numpy.identity(4)):
                sm.shapeType = model.ShapeModel.SP_SPHERE
                sm.data = model.SphereData()
                sm.data.radius = sdata.primitiveParameters[0]
                sm.data.material = self._materials[sdata.appearanceIndex]
            elif sdata.primitiveType == OpenHRP.SP_CYLINDER and numpy.allclose(sm.matrix, numpy.identity(4)):
                sm.shapeType = model.ShapeModel.SP_CYLINDER
                sm.data = model.CylinderData()
                sm.data.radius = sdata.primitiveParameters[0]
                sm.data.height = sdata.primitiveParameters[1]
                sm.data.material = self._materials[sdata.appearanceIndex]
            elif sdata.primitiveType == OpenHRP.SP_BOX and numpy.allclose(sm.matrix, numpy.identity(4)):
                sm.shapeType = model.ShapeModel.SP_BOX
                sm.data = model.BoxData()
                sm.data.x = sdata.primitiveParameters[0]
                sm.data.y = sdata.primitiveParameters[1]
                sm.data.z = sdata.primitiveParameters[2]
                sm.data.material = self._materials[sdata.appearanceIndex]
            else:
                # raise Exception('unsupported shape primitive: %s' % sdata.primitiveType)
                sm.shapeType = model.ShapeModel.SP_MESH
                sm.data = self.readMesh(sdata)
            lm.visuals.append(sm)
            lm.collisions.append(sm)
        return lm

    def readMesh(self, sdata):
        data = model.MeshData()
        data.vertex = numpy.array(sdata.vertices).reshape(len(sdata.vertices)/3, 3)
        data.vertex_index = numpy.array(sdata.triangles).reshape(len(sdata.triangles)/3, 3)
        adata = self._hrpapperances[sdata.appearanceIndex]
        if adata.normalPerVertex is True:
            data.normal = numpy.array(adata.normals).reshape(len(adata.normals)/3, 3)
            if len(adata.normalIndices) > 0:
                data.normal_index = numpy.array(adata.normalIndices).reshape(len(adata.normalIndices)/3, 3)
            else:
                data.normal_index = data.vertex_index
        else:
            data.normal = numpy.array(adata.normals).reshape(len(adata.normals)/3, 3)
            if len(adata.normalIndices) > 0:
                idx = []
                for i in adata.normalIndices:
                    idx.append(i)
                    idx.append(i)
                    idx.append(i)
                data.normal_index = numpy.array(idx).reshape(len(idx)/3, 3)
            else:
                idx = []
                for i in range(0, len(adata.normals)/3):
                    idx.append(i)
                    idx.append(i)
                    idx.append(i)
                data.normal_index = numpy.array(idx).reshape(len(idx)/3, 3)
#        if len(data.vertex_index) != len(data.normal_index):
#            raise Exception('vertex length and normal length not match')
        if adata.materialIndex >= 0:
            data.material = self._materials[adata.materialIndex]
        if data.material is not None and adata.textureIndex >= 0:
            fname = self._hrptextures[adata.textureIndex].url
            if self._assethandler:
                data.material.texture = self._assethandler(fname)
            else:
                data.material.texture = fname
            data.uvmap = numpy.array(adata.textureCoordinate).reshape(len(adata.textureCoordinate)/2, 2)
            data.uvmap_index = numpy.array(adata.textureCoordIndices).reshape(len(adata.textureCoordIndices)/3, 3)
        return data

    def readChild(self, parent, child):
        # first, create joint pairs
        jm = model.JointModel()
        jm.parent = parent.name
        jm.child = child.name
        jm.name = child.name
        jm.jointId = child.jointId
        jm.axis = model.AxisData()
        try:
            jm.axis.limit = [child.ulimit[0], child.llimit[0]]
        except IndexError:
            pass
        try:
            jm.axis.velocitylimit = [child.uvlimit[0], child.lvlimit[0]]
        except IndexError:
            pass
        try:
            jm.axis.effortlimit = [child.climit[0]*child.gearRatio*child.torqueConst]
        except IndexError:
            pass
        jm.axis.axis = child.jointAxis
        if child.jointType == 'fixed':
            jm.jointType = model.JointModel.J_FIXED
        elif child.jointType == 'rotate':
            if jm.axis.limit is None or (jm.axis.limit[0] is None and jm.axis.limit[1] is None):
                jm.jointType = model.JointModel.J_CONTINUOUS
            else:
                jm.jointType = model.JointModel.J_REVOLUTE
        elif child.jointType == 'slide':
            jm.jointType = model.JointModel.J_PRISMATIC
        elif child.jointType == 'crawler':
            jm.jointType = model.JointModel.J_CRAWLER
        elif child.jointType == 'pseudoContinuousTrack':
            jm.jointType = model.JointModel.J_CRAWLER
        else:
            raise Exception('unsupported joint type: %s' % child.jointType)
        jm.trans = numpy.array(child.translation)
        jm.rot = tf.quaternion_about_axis(child.rotation[3], child.rotation[0:3])
        # convert to absolute position
        jm.matrix = numpy.dot(parent.getmatrix(), jm.getmatrix())
        jm.trans = None
        jm.rot = None
        self._joints.append(jm)
        # then, convert link shape information
        lm = self.readLink(child)
        lm.matrix = jm.getmatrix()
        lm.trans = None
        lm.rot = None
        self._links.append(lm)
        for c in child.childIndices:
            self.readChild(jm, self._hrplinks[c])

    def resolveModelLoader(self):
        nsobj = self._orb.resolve_initial_references("NameService")
        self._ns = nsobj._narrow(CosNaming.NamingContext)
        try:
            obj = self._ns.resolve([CosNaming.NameComponent("ModelLoader", "")])
            self._loader = obj._narrow(OpenHRP.ModelLoader)
        except CosNaming.NamingContext.NotFound:
            logging.error("unable to resolve OpenHRP model loader on CORBA name service")
            raise


class VRMLWriter(object):
    '''
    VRML writer class
    '''
    def __init__(self):
        self._linkmap = {}
        self._roots = []
        self._ignore = []
        self._options = None

    def write(self, mdata, fname, options=None):
        '''
        Write simulation model in VRML format
        '''
        self._options = options
        fpath, fext = os.path.splitext(fname)
        basename = os.path.basename(fpath)
        dirname = os.path.dirname(fname)
        if mdata.name is None or mdata.name == '':
            mdata.name = basename

        # convert revolute2 joint to two revolute joints (with a link
        # in between)
        for j in mdata.joints:
            if j.jointType == model.JointModel.J_REVOLUTE2:
                logging.info("converting revolute2 joint to two revolute joints")
                nl = model.LinkModel()
                nl.name = j.name + "_REVOLUTE2_LINK"
                nl.matrix = j.getmatrix()
                nl.trans = None
                nl.rot = None
                nl.mass = 0.001 # assign very small mass
                mdata.links.append(nl)
                nj = copy.deepcopy(j)
                nj.name = j.name + "_SECOND"
                nj.jointType = model.JointModel.J_REVOLUTE
                nj.parent = nl.name
                nj.child = j.child
                nj.axis = j.axis2
                mdata.joints.append(nj)
                j.jointType = model.JointModel.J_REVOLUTE
                j.child = nl.name

        # check for same names in visuals or collisions
        usednames = {}
        for l in mdata.links:
            for v in l.visuals:
                if v.name in usednames:
                    v.name = l.name + "-visual"
                    if v.name in usednames:
                        v.name = l.name + "-visual-" + str(uuid.uuid1()).replace('-', '')
                usednames[v.name] = True
            for c in l.collisions:
                if c.name in usednames:
                    c.name = l.name + "-collision"
                    if c.name in usednames:
                        c.name = l.name + "-collision-" + str(uuid.uuid1()).replace('-', '')
                usednames[c.name] = True

        # find root joint (including local peaks)
        self._roots = utils.findroot(mdata)

        # render the data structure using template
        loader = jinja2.PackageLoader(self.__module__, 'template')
        env = jinja2.Environment(loader=loader, extensions=['jinja2.ext.do'])

        self._linkmap['world'] = model.LinkModel()
        for m in mdata.links:
            self._linkmap[m.name] = m

        # render shape vrml file for each links
        shapefilemap = {}
        for l in mdata.links:
            shapes = copy.copy(l.visuals)
            if options is not None and options.usecollision:
                shapes = copy.copy(l.collisions)
            if options is not None and options.useboth:
                shapes.extend(copy.copy(l.collisions))
            for v in shapes:
                logging.info('writing shape of link: %s, type: %s' % (l.name, v.shapeType))
                if v.shapeType == model.ShapeModel.SP_MESH:
                    template = env.get_template('vrml-mesh.wrl')
                    if isinstance(v.data, model.MeshTransformData):
                        v.data.pretranslate()
                    m = {}
                    m['children'] = [v.data]
                    shapefname = (mdata.name + "-" + l.name + "-" + v.name + ".wrl").replace('::', '_')
                    with open(os.path.join(dirname, shapefname), 'w') as ofile:
                        ofile.write(template.render({
                            'name': v.name,
                            'ShapeModel': model.ShapeModel,
                            'mesh': m
                        }))
                    shapefilemap[v.name] = shapefname

        # render main vrml file for each bodies
        template = env.get_template('vrml.wrl')
        roots = []
        modelfiles = {}
        for root in self._roots:
            if root == 'world':
                for r in utils.findchildren(mdata, root):
                    roots.append((r.child, "fixed"))
            else:
                roots.append((root, "free"))
        for r in roots:
            logging.info('writing model for %s' % r[0])
            if len(roots) == 1:
                mfname = fname
            else:
                mfname = (mdata.name + "-" + r[0] + ".wrl").replace('::', '_')
            self.renderchildren(mdata, r[0], r[1], os.path.join(dirname, mfname), shapefilemap, template)
            modelfiles[mfname] = self._linkmap[r[0]]
        
        # render openhrp project
        template = env.get_template('openhrp-project.xml')
        with open(fname.replace('.wrl', '-project.xml'), 'w') as ofile:
            ofile.write(template.render({
                'models': modelfiles,
            }))

        # render choreonoid project
        template = env.get_template('choreonoid-project.yaml')
        with open(fname.replace('.wrl', '-project.cnoid'), 'w') as ofile:
            ofile.write(template.render({
                'models': modelfiles,
            }))

    def convertchildren(self, mdata, pjoint, joints, links):
        children = []
        plink = self._linkmap[pjoint.child]
        for cjoint in utils.findchildren(mdata, pjoint.child):
            nmodel = {}
            try:
                clink = self._linkmap[cjoint.child]
            except KeyError:
                logging.warning("unable to find child link %s" % cjoint.child)
            (cchildren, joints, links) = self.convertchildren(mdata, cjoint, joints, links)
            pjointinv = numpy.linalg.pinv(pjoint.getmatrix())
            cjointinv = numpy.linalg.pinv(cjoint.getmatrix())
            cjoint2 = copy.deepcopy(cjoint)
            cjoint2.matrix = numpy.dot(pjointinv, cjoint.getmatrix())
            cjoint2.trans = None
            cjoint2.rot = None
            clink2 = copy.deepcopy(clink)
            clink2.matrix = numpy.dot(cjointinv, clink.getmatrix())
            clink2.trans = None
            clink2.rot = None
            if clink2.mass == 0:
                logging.warning("detect link with mass zero, assigning small (0.001) mass.")
                clink2.mass = 0.001
            if not numpy.allclose(clink2.getmatrix(), numpy.identity(4)):
                clink2.translate(clink2.getmatrix())
            nmodel['joint'] = cjoint2
            nmodel['jointtype'] = self.convertjointtype(cjoint.jointType)
            nmodel['link'] = clink2
            nmodel['children'] = cchildren
            children.append(nmodel)
            joints.append(cjoint.name)
            links.append(cjoint.child)
        return (children, joints, links)

    def renderchildren(self, mdata, root, jointtype, fname, shapefilemap, template):
        nmodel = {}
        rootlink = self._linkmap[root]
        rootjoint = model.JointModel()
        rootjoint.name = root
        rootjoint.jointType = jointtype
        rootjoint.matrix = rootlink.getmatrix()
        rootjoint.trans = None
        rootjoint.rot = None
        rootjoint.child = root
        (children, joints, links) = self.convertchildren(mdata, rootjoint, [], [])
        nmodel['link'] = rootlink
        nmodel['joint'] = rootjoint
        nmodel['jointtype'] = rootjoint.jointType
        nmodel['children'] = children

        # assign jointId
        if jointtype in ['free', 'fixed']:
            jointmap = {}
            jointcount = 0
        else:
            jointmap = {root: 0}
            jointcount = 1
        for j in joints:
            jointmap[j] = 0
        for j in joints:
            jointmap[j] = jointcount
            jointcount = jointcount + 1

        with open(fname, 'w') as ofile:
            ofile.write(template.render({
                'model': {'name':rootlink.name, 'children':[nmodel]},
                'body': mdata,
                'links': links,
                'joints': joints,
                'jointmap': jointmap,
                'ShapeModel': model.ShapeModel,
                'shapefilemap': shapefilemap,
                'options': self._options
            }))

    def convertjointtype(self, t):
        if t == model.JointModel.J_FIXED:
            return "fixed"
        elif t == model.JointModel.J_REVOLUTE:
            return "rotate"
        elif t == model.JointModel.J_PRISMATIC:
            return "slide"
        elif t == model.JointModel.J_SCREW:
            return "rotate"
        elif t == model.JointModel.J_CONTINUOUS:
            return "rotate"
        else:
            raise Exception('unsupported joint type: %s' % t)

class VRMLMeshWriter(object):
    '''
    VRML mesh writer class
    '''
    def __init__(self):
        self._linkmap = {}
        self._roots = []
        self._ignore = []

    def write(self, m, fname, options=None):
        '''
        Write mesh in VRML format
        '''
        fpath, fext = os.path.splitext(fname)
        basename = os.path.basename(fpath)
        dirname = os.path.dirname(fname)

        # render the data structure using template
        loader = jinja2.PackageLoader(self.__module__, 'template')
        env = jinja2.Environment(loader=loader, extensions=['jinja2.ext.do'])
        template = env.get_template('vrml-mesh.wrl')
        if m.shapeType == model.ShapeModel.SP_MESH:
            if isinstance(m.data, model.MeshTransformData):
                m.data.pretranslate()
            nm = {}
            nm['children'] = [m.data]
            with open(fname, 'w') as ofile:
                ofile.write(template.render({
                    'name': basename,
                    'ShapeModel': model.ShapeModel,
                    'mesh': nm
                }))
