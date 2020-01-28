import numpy as np
from scipy.sparse import coo_matrix, csc_matrix, csr_matrix, spdiags, eye, tril, triu
from ..common import ranges
from .mesh_tools import unique_row, find_entity, show_mesh_2d
from ..quadrature import TriangleQuadrature
from .Mesh2d import Mesh2d

class HalfEdgePolygonMesh(Mesh2d):
    def __init__(self, node, halfedge, NC):
        """

        Parameters
        ----------
        node : (NN, GD)
        halfedge : (2*NE, 6),
        """
        self.node = node
        self.ds = HalfEdgePolygonMeshDataStructure(node.shape[0], NC, halfedge)
        self.meshtype = 'hepolygon'
        self.itype = halfedge.dtype
        self.ftype = node.dtype

    @classmethod
    def from_polygonmesh(cls, mesh):
        NC = mesh.number_of_cells()
        NN = mesh.number_of_nodes()
        NE = mesh.number_of_edges()
        NV = mesh.number_of_vertices_of_cells()

        node = mesh.entity('node')
        edge = mesh.entity('edge')
        cell, cellLocation = mesh.entity('cell')
        cell2edge = mesh.ds.cell_to_edge(sparse=False)
        edge2cell = mesh.ds.edge_to_cell()
        cell2edgeSign = mesh.ds.cell_to_edge_sign(sparse=False)
        cell2edgeSign[cell2edgeSign==1] = 0
        cell2edgeSign[cell2edgeSign==-1] = NE

        isInEdge = edge2cell[:, 0] != edge2cell[:, 1]

        nex, pre = mesh.ds.boundary_edge_to_edge()

        halfedge = np.zeros((2*NE, 6), dtype=mesh.itype)
        # 指向的顶点
        halfedge[:NE, 0] = edge[:, 1]
        halfedge[NE:, 0] = edge[:, 0]

        # 指向的单元
        halfedge[:NE, 1] = edge2cell[:, 0]
        halfedge[NE:, 1] = edge2cell[:, 1]
        halfedge[NE:, 1][~isInEdge] = NC

        # 在指向单元中的下一条边
        idx = cellLocation[edge2cell[:, 0]] + (edge2cell[:, 2] + 1)%NV[edge2cell[:,  0]]
        halfedge[:NE, 2] = cell2edge[idx] + cell2edgeSign[idx]

        idx = cellLocation[edge2cell[isInEdge, 1]] + (edge2cell[isInEdge, 3] + 1)%NV[edge2cell[isInEdge,  1]]
        halfedge[NE:, 2][isInEdge] = cell2edge[idx] + cell2edgeSign[idx]
        halfedge[NE:, 2][~isInEdge] = NE + nex

        # 在指向单元中的上一条边
        idx = cellLocation[edge2cell[:, 0]] + (edge2cell[:, 2] - 1)%NV[edge2cell[:,  0]]
        halfedge[:NE, 3] = cell2edge[idx] + cell2edgeSign[idx]

        idx = cellLocation[edge2cell[isInEdge, 1]] + (edge2cell[isInEdge, 3] - 1)%NV[edge2cell[isInEdge,  1]]
        halfedge[NE:, 3][isInEdge] = cell2edge[idx] + cell2edgeSign[idx]
        halfedge[NE:, 3][~isInEdge] = NE + pre

        # 相反的halfedge
        halfedge[:NE, 4] = range(NE, 2*NE)
        halfedge[NE:, 4] = range(NE)

        # 标记主半边 ：1：主半边， 0：对偶半边
        halfedge[:NE, 5] = 1
        return cls(node, halfedge, NC)

    def entity(self, etype=2):
        if etype in {'cell', 2}:
            return self.ds.cell_to_node(sparse=False)
        elif etype in {'edge', 'face', 1}:
            return self.ds.edge_to_node(sparse=False)
        elif etype in {'node', 0}:
            return self.node
        else:
            raise ValueError("`entitytype` is wrong!")

    def entity_barycenter(self, etype='cell', index=None):
        node = self.node
        dim = self.geo_dimension()
        if etype in {'cell', 2}:
            cell2node = self.ds.cell_to_node()
            NV = self.ds.number_of_vertices_of_cells().reshape(-1,1)
            bc = cell2node*node/NV
        elif etype in {'edge', 'face', 1}:
            edge = self.ds.edge_to_node(sparse=False)
            bc = np.sum(node[edge, :], axis=1).reshape(-1, dim)/edge.shape[1]
        elif etype in {'node', 1}:
            bc = node
        return bc

    def cell_area(self, index=None):
        NC = self.number_of_cells()
        node = self.entity('node')
        halfedge = self.ds.halfedge

        e0 = halfedge[halfedge[:, 3], 0]
        e1 = halfedge[:, 0]

        w = np.array([[0, -1], [1, 0]], dtype=np.int)
        v= (node[e1] - node[e0])@w
        val = np.sum(v*node[e0], axis=1)

        a = np.zeros(NC+1, dtype=self.ftype)
        np.add.at(a, halfedge[:, 1], val)
        a /=2
        return a[:-1]

    def edge_bc_to_point(self, bcs, index=None):
        node = self.entity('node')
        edge = self.entity('edge')
        index = index if index is not None else np.s_[:]
        ps = np.einsum('ij, kjm->ikm', bcs, node[edge[index]])
        return ps

    def refine(self, isMarkedCell, dflag=False):
        isMarkedCell = np.r_['0', isMarkedCell, False]
        GD = self.geo_dimension()
        NN = self.number_of_nodes()
        NE = self.number_of_edges()
        NC = self.number_of_cells()
        NV = self.number_of_vertices_of_cells()

        halfedge = self.ds.halfedge
        isMainHEdge = (halfedge[:, 5] == 1)
        isInHEdge = (halfedge[:, 1] != NC)


        # 标记边
        isMarkedHEdge = isMarkedCell[halfedge[:, 1]]
        flag = ~isMarkedHEdge & isMarkedHEdge[halfedge[:, 4]]
        isMarkedHEdge[flag] = True

        node = self.entity('node')
        flag = isMainHEdge & isMarkedHEdge
        idx = halfedge[flag, 4]
        ec = (node[halfedge[flag, 0]] + node[halfedge[idx, 0]])/2
        NE1 = len(ec)

        bc = self.entity_barycenter('cell')
        # 细分边
        if dflag:
            print('MarkedEdge:', halfedge[isMarkedHEdge])

        halfedge1 = np.zeros((2*NE1, 6), dtype=self.itype)
        flag = isMainHEdge[isMarkedHEdge]
        halfedge1[flag, 0] = range(NN, NN+NE1)
        idx0 = np.argsort(idx)
        halfedge1[~flag, 0] = halfedge1[flag, 0][idx0]
        halfedge1[:, 1] = halfedge[isMarkedHEdge, 1]
        halfedge1[:, 3] = halfedge[isMarkedHEdge, 3] # 前一个 
        halfedge1[:, 4] = halfedge[isMarkedHEdge, 4] # 对偶边
        halfedge1[:, 5] = halfedge[isMarkedHEdge, 5] # 主边标记

        halfedge[isMarkedHEdge, 3] = range(2*NE, 2*NE + 2*NE1)
        idx = halfedge[isMarkedHEdge, 4] # 原始对偶边
        halfedge[isMarkedHEdge, 4] = halfedge[idx, 3]  # 原始对偶边的前一条边是新的对偶边

        halfedge = np.r_['0', halfedge, halfedge1]
        halfedge[halfedge[:, 3], 2] = range(2*NE+2*NE1)

        if dflag:
            self.node = np.r_['0', node, ec]
            self.ds.reinit(NN+NE1, NC, halfedge)
            return

        # 细分单元
        N = halfedge.shape[0]
        NV = self.ds.number_of_vertices_of_cells(returnall=True)
        NHE = sum(NV[isMarkedCell])
        halfedge2 = np.zeros((2*NHE, 6), dtype=self.itype)

        NC1 = isMarkedCell.sum()
        flag0 = (halfedge[:, 0] >= NN) & isMarkedCell[halfedge[:, 1]]
        nex0 = halfedge[flag0, 2]
        pre0 = halfedge[flag0, 3]
        flag1 = (halfedge[halfedge[:, 3], 0] >= NN) & isMarkedCell[halfedge[halfedge[:, 3], 1]]
        nex1 = halfedge[flag1, 2]
        pre1 = halfedge[flag1, 3]

        flag = (halfedge[:, 1] == NC)
        halfedge[flag, 1] = NC + NHE

        cell2newNode = np.full(NC+1, NN+NE1, dtype=self.itype)
        cell2newNode[isMarkedCell] += range(isMarkedCell.sum()) 
        idx = halfedge[flag0, 1] 
        halfedge[flag0, 1] = range(NC, NC + NHE)
        halfedge[pre0, 1] = halfedge[flag0, 1]
        halfedge[flag0, 2] = range(N, N+NHE)
        halfedge[flag1, 3] = range(N+NHE, N+2*NHE)
        halfedge2[:NHE, 0] = cell2newNode[idx]
        halfedge2[:NHE, 1] = halfedge[flag0, 1]
        halfedge2[:NHE, 2] = halfedge[pre0, 3]
        halfedge2[:NHE, 3], = np.nonzero(flag0)
        halfedge2[:NHE, 4] = halfedge[nex0, 3]
        halfedge2[:NHE, 5] = 1

        halfedge2[NHE:, 0] = halfedge[pre1, 0]
        halfedge2[NHE:, 1] = halfedge[flag1, 1]
        halfedge2[NHE:, 2], = np.nonzero(flag1)
        halfedge2[NHE:, 3] = halfedge[nex1, 2]
        halfedge2[NHE:, 4] = halfedge[pre1, 2]
        halfedge2[NHE:, 5] = 0

        halfedge = np.r_['0', halfedge, halfedge2]

        flag = np.zeros(NC+NHE+1, dtype=np.bool)
        np.add.at(flag, halfedge[:, 1], True)
        idxmap = np.zeros(NC+NHE+1, dtype=self.itype)
        NC = flag.sum()

        idxmap[flag] = range(NC)
        halfedge[:, 1] = idxmap[halfedge[:, 1]]

        self.node = np.r_['0', node, ec, bc[isMarkedCell[:-1]]]
        self.ds.reinit(NN+NE1+NC1, NC-1, halfedge)

    def refine_1(self, isMarkedCell):
        isMarkedCell = np.r_['0', isMarkedCell, False]
        GD = self.geo_dimension()
        NN = self.number_of_nodes()
        NE = self.number_of_edges()
        NC = self.number_of_cells()
        NV = self.number_of_vertices_of_cells()

        halfedge = self.ds.halfedge
        isInHEdge = (halfedge[:, 1] != NC)

        # 标记边
        isMarkedHEdge = isMarkedCell[halfedge[:, 1]]
        flag = ~isMarkedHEdge & isMarkedHEdge[halfedge[:, 4]]
        isMarkedHEdge[flag] = True

        # 细分边
        node = self.entity('node')
        isMarkedHEdge0 = isMarkedHEdge[:NE]
        halfedge0 = halfedge[:NE]
        halfedge1 = halfedge[NE:]
        ec = (node[halfedge0[isMarkedHEdge0, 0]] + node[halfedge1[isMarkedHEdge0, 0]])/2
        NE1 = len(ec)

        halfedge = np.zeros((2*(NE + NE1), 5), dtype=self.itype)
        idx, = np.nonzero(isMarkedHEdge0)
        halfedge[NE:NE+NE1, 0] = range(NN, NN+NE1)
        halfedge[NE:NE+NE1, 1] = halfedge0[isMarkedHEdge0, 1]
        halfedge[NE:NE+NE1, 2] = idx
        halfedge[NE:NE+NE1, 3] = halfedge0[isMarkedHEdge0, 3]
        flag = halfedge0[isMarkedHEdge0, 3] >= NE
        halfedge[NE:NE+NE1, 3][flag] += NE1
        halfedge[NE:NE+NE1, 4] = NE + NE1 + idx

        halfedge[2*NE+NE1:, 0] = range(NN, NN+NE1)
        halfedge[2*NE+NE1:, 1] = halfedge1[isMarkedHEdge0, 1]
        halfedge[2*NE+NE1:, 2] = NE1 + NE + idx
        flag = halfedge1[isMarkedHEdge0, 3] >= NE
        halfedge[2*NE+NE1:, 3] = halfedge1[isMarkedHEdge0, 3]
        halfedge[2*NE+NE1:, 3][flag] += NE1
        halfedge[2*NE+NE1:, 4] = idx

        halfedge[:NE, 0] = halfedge0[:, 0]
        halfedge[:NE, 1] = halfedge0[:, 1]
        halfedge[:NE, 3] = halfedge0[:, 3]
        flag = halfedge0[:, 3] >= NE
        halfedge[:NE, 3][flag] += NE1
        halfedge[:NE, 3][isMarkedHEdge0] = range(NE, NE+NE1)
        halfedge[:NE, 4] = halfedge0[:, 4]
        flag = halfedge0[:, 4] >= NE
        halfedge[:NE, 4][flag] += NE1
        halfedge[:NE, 4][isMarkedHEdge0] = range(2*NE+NE1, 2*NE+2*NE1)


        halfedge[NE+NE1:2*NE+NE1, 0] = halfedge1[:, 0]
        halfedge[NE+NE1:2*NE+NE1, 1] = halfedge1[:, 1]
        halfedge[NE+NE1:2*NE+NE1, 3] = halfedge1[:, 3]
        flag = halfedge1[:, 3] >= NE
        halfedge[NE+NE1:2*NE+NE1, 3][flag] += NE1
        halfedge[NE+NE1:2*NE+NE1, 3][isMarkedHEdge0] = range(2*NE+NE1,
                2*NE+2*NE1)
        halfedge[NE+NE1:2*NE+NE1, 4] = halfedge1[:, 4]
        flag = halfedge1[:, 4] >= NE
        halfedge[NE+NE1:2*NE+NE1, 4][flag] += NE1
        halfedge[NE+NE1:2*NE+NE1, 4][isMarkedHEdge0] = range(NE, NE+NE1)


        halfedge[halfedge[:, 3], 2] = range(2*NE+2*NE1)
        self.node = np.r_['0', node, ec]
        self.ds.reinit(NN+NE1, NC, halfedge)

        # 细分单元
        NC = self.number_of_cells()
        NC1 = isMarkedCell.sum()
        NV1 = self.number_of_nodes_of_cells()[isMarkedCell]
        begin = self.ds.cell2hedge[isMarkedCell]
        end = begin.copy()
        isNotOK = np.zeros(NC1, dtype=np.bool)
        while isNotOK.sum() > 0:
            pass


    def print(self):
        cell, cellLocation = self.entity('cell')
        print("cell:\n", cell)
        print("cellLocation:\n", cellLocation)
        print("cell2edge:\n", self.ds.cell_to_edge(sparse=False))
        print("cell2hedge:\n")
        for i, val in enumerate(self.ds.cell2hedge[:-1]):
            print(i, ':', val)

        print("edge:")
        for i, val in enumerate(self.entity('edge')):
            print(i, ":", val)
        print("halfedge:")
        for i, val in enumerate(self.ds.halfedge):
            print(i, ":", val)

class HalfEdgePolygonMeshDataStructure():
    def __init__(self, NN, NC, halfedge):
        self.NN = NN
        self.NC = NC
        self.NE = len(halfedge)//2
        self.NF = self.NE
        self.halfedge = halfedge
        self.itype = halfedge.dtype

        self.cell2hedge = np.zeros(NC+1, dtype=self.itype)
        self.cell2hedge[halfedge[:, 1]] = range(2*self.NE)

    def reinit(self, NN, NC, halfedge):
        self.NN = NN
        self.NC = NC
        self.NE = len(halfedge)//2
        self.NF = self.NE
        self.halfedge = halfedge
        self.itype = halfedge.dtype

        self.cell2hedge = np.zeros(NC+1, dtype=self.itype)
        self.cell2hedge[halfedge[:, 1]] = range(2*self.NE)

    def number_of_vertices_of_cells(self, returnall=False):
        NC = self.NC
        halfedge = self.halfedge
        NV = np.zeros(NC+1, dtype=self.itype)
        np.add.at(NV, halfedge[:, 1], 1)
        if returnall:
            return NV
        else:
            return NV[:NC]

    def number_of_nodes_of_cells(self):
        return self.number_of_vertices_of_cells()

    def number_of_edges_of_cells(self):
        return self.number_of_vertices_of_cells()

    def number_of_face_of_cells(self):
        return self.number_of_vertices_of_cells()

    def cell_to_node(self, sparse=True):
        NN = self.NN
        NC = self.NC
        NE = self.NE

        halfedge = self.halfedge
        isInHEdge = (halfedge[:, 1] != NC)

        if sparse:
            val = np.ones(isInHEdge.sum(), dtype=np.bool)
            I = halfedge[isInHEdge, 1]
            J = halfedge[isInHEdge, 0]
            cell2node = csr_matrix((val, (I.flat, J.flat)), shape=(NC, NN), dtype=np.bool)
            return cell2node
        else:
            NV = self.number_of_vertices_of_cells()
            cellLocation = np.zeros(NC+1, dtype=self.itype)
            cellLocation[1:] = np.cumsum(NV)
            cell2node = np.zeros(cellLocation[-1], dtype=self.itype)
            current = self.cell2hedge.copy()[:NC]
            idx = cellLocation[:-1].copy()
            cell2node[idx] = halfedge[current, 0]
            NV0 = np.ones(NC, dtype=self.itype)
            isNotOK = NV0 < NV
            while isNotOK.sum() > 0:
               current[isNotOK] = halfedge[current[isNotOK], 2]
               idx[isNotOK] += 1
               NV0[isNotOK] += 1
               cell2node[idx[isNotOK]] = halfedge[current[isNotOK], 0]
               isNotOK = (NV0 < NV)
            return cell2node, cellLocation

    def cell_to_edge(self, sparse=True):
        NE = self.NE
        NC = self.NC

        halfedge = self.halfedge

        J = np.zeros(2*NE, dtype=self.itype)
        isMainHEdge = (halfedge[:, 5] == 1)
        J[isMainHEdge] = range(NE)
        J[halfedge[isMainHEdge, 4]] = range(NE)
        if sparse:
            isInHEdge = (halfedge[:, 1] != NC)
            val = np.ones(2*NE, dtype=np.bool)
            cell2edge = csr_matrix((val[isInHEdge], (halfedge[isInHEdge, 1],
                J[isInHEdge])), shape=(NC, NE), dtype=np.bool)
            return cell2edge
        else:
            NV = self.number_of_vertices_of_cells()
            cellLocation = np.zeros(NC+1, dtype=self.itype)
            cellLocation[1:] = np.cumsum(NV)
            cell2edge = np.zeros(cellLocation[-1], dtype=self.itype)
            current = halfedge[self.cell2hedge[:-1], 2] # 下一个边
            idx = cellLocation[:-1]
            cell2edge[idx] = J[current]
            NV0 = np.ones(NC, dtype=self.itype)
            isNotOK = NV0 < NV
            while isNotOK.sum() > 0:
                current[isNotOK] = halfedge[current[isNotOK], 2]
                idx[isNotOK] += 1
                NV0[isNotOK] += 1
                cell2edge[idx[isNotOK]] = J[current[isNotOK]]
                isNotOK = (NV0 < NV)
            return cell2edge

    def cell_to_face(self, sparse=True):
        return self.cell_to_edge(sparse=sparse)

    def cell_to_cell(self):
        NC = self.NC
        halfedge = self.halfedge
        isInHEdge = (halfedge[:, 1] != NC)
        val = np.ones(isInHEdge.sum(), dtype=np.bool)
        I = halfedge[isInHEdge, 1]
        J = halfedge[halfedge[isInHEdge, 4], 1]
        cell2cell = coo_matrix((val, (I, J)), shape=(NC, NC), dtype=np.bool)
        cell2cell+= coo_matrix((val, (J, I)), shape=(NC, NC), dtype=np.bool)
        return cell2cell.tocsr()

    def edge_to_node(self, sparse=False):
        NN = self.NN
        NE = self.NE
        halfedge = self.halfedge
        isMainHEdge = halfedge[:, 5] == 1
        if sparse == False:
            edge = np.zeros((NE, 2), dtype=self.itype)
            edge[:, 0] = halfedge[halfedge[isMainHEdge, 4], 0]
            edge[:, 1] = halfedge[isMainHEdge, 0]
            return edge
        else:
            val = np.ones((NE,), dtype=np.bool)
            edge2node = coo_matrix((val, (range(NE), halfedge[isMainHEdge,0])), shape=(NE, NN), dtype=np.bool)
            edge2node+= coo_matrix((val, (range(NE), halfedge[halfedge[isMainHEdge, 4], 0])), shape=(NE, NN), dtype=np.bool)
            return edge2node.tocsr()

    def edge_to_edge(self):
        edge2node = self.edge_to_node()
        return edge2node*edge2node.tranpose()

    def edge_to_cell(self, sparse=False):
        NE = self.NE
        NC = self.NC
        halfedge = self.halfedge

        J = np.zeros(2*NE, dtype=self.itype)
        isMainHEdge = (halfedge[:, 5] == 1)
        J[isMainHEdge] = range(NE)
        J[~isMainHEdge] = J[halfedge[~isMainHEdge, 4]]
        if sparse == False:
            edge2cell = np.zeros((NE, 4), dtype=self.itype)
            edge2cell[:, 0] = halfedge[isMainHEdge, 1]
            edge2cell[:, 1] = halfedge[~isMainHEdge, 1]
            isBdEdge = (edge2cell[:, 1] == NC)
            edge2cell[isBdCell, 1] = edge2cell[isBdCell, 0]
            return edge2cell
        else:
            isInHEdge = (halfedge[:, 1] != NC)
            val = np.ones(2*NE, dtype=np.bool)
            edge2cell = csr_matrix((val[isInHEdge], (J[isInHEdge], halfedge[isInHEdge, 1])), shape=(NE, NC), dtype=np.bool)
            return edge2cell

    def node_to_node(self):
        NN = self.NN
        NE = self.NE

        edge = self.edge_to_node()
        I = edge[:, 0:2].flat
        J = edge[:, 1::-1].flat
        val = np.ones(2*NE, dtype=np.bool)
        node2node = csr_matrix((val, (I, J)), shape=(NN, NN), dtype=np.bool)
        return node2node

    def node_to_cell(self, sparse=True):

        NN = self.NN
        NC = self.NC
        NE = self.NE

        isInHEdge = (halfedge[:, 1] != NC)
        val = np.ones(isInHEdge.sum(), dtype=np.bool)
        I = halfedge[isInHEdge, 0]
        J = halfedge[isInHEdge, 1]
        cell2node = csr_matrix((val, (I.flat, J.flat)), shape=(NC, NN), dtype=np.bool)
        return node2cell


    def boundary_node_flag(self):
        NN = self.NN
        edge = self.edge_to_node()
        isBdEdge = self.boundary_edge_flag()
        isBdNode = np.zeros(NN, dtype=np.bool)
        isBdNode[edge[isBdEdge,:]] = True
        return isBdNode

    def boundary_edge_flag(self):
        NE = self.NE
        edge2cell = self.edge_to_cell()
        return edge2cell[:, 0] == edge2cell[:, 1]

    def boundary_edge(self):
        edge = self.edge_to_node()
        return edge[self.boundary_edge_index()]

    def boundary_cell_flag(self):
        NC = self.NC
        edge2cell = self.edge_to_cell()
        isBdEdge = edge2cell[:, 0] == edge2cell[:, 1]
        isBdCell = np.zeros(NC, dtype=np.bool)
        isBdCell[edge2cell[isBdEdge, 0:2]] = True
        return isBdCell

    def boundary_node_index(self):
        isBdNode = self.boundary_node_flag()
        idx, = np.nonzero(isBdNode)
        return idx

    def boundary_edge_index(self):
        isBdEdge = self.boundary_edge_flag()
        idx, = np.nonzero(isBdEdge)
        return idx

    def boundary_cell_index(self):
        isBdCell = self.boundary_cell_flag()
        idx, = np.nonzero(isBdCell)
        return idx