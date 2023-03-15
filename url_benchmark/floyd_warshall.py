class Edge:
    def __init__(self, start, end, weight):
        self.start = start
        self.end = end
        self.weight = weight

class Graph:
    def __init__(self):
        self.adj = {} #Adjacency matrix that holds graph data
        self.vertexCount = 0

    def add_vertex(self, vertex):
        if vertex in self.adj:
            return "Vertex already exists"
        if vertex != self.vertexCount:
            return "Don't skip a vertex"
        self.adj[vertex] = []
        self.vertexCount += 1

    def add_edge(self, start, end, weight):
        if start not in self.adj:
            return "Starting vertex not in graph"
        if end not in self.adj:
            return "Ending vertex not in graph"
        if start == end:
            return "Cannot have same start and end vertex"
        edge = Edge(start, end, weight)
        self.adj[start].append(edge)

    def doesEdgeExist(self, start, end):
        for vertex in self.adj:
            for edge in self.adj[vertex]:
                if edge.start == start and edge.end == end:
                    return (True, edge)
        return (False, None)

    def floydwarshall(self):
        M = [[9999999 for x in range(len(self.adj))] for y in range(len(self.adj))]
        P = [[9999999 for x in range(len(self.adj))] for y in range(len(self.adj))]
        for x in range(len(M)):
            for y in range(len(M[0])):
                if x == y:
                    M[x][y] = 0
                exists, edge = self.doesEdgeExist(x, y)
                if exists:
                    M[x][y] = edge.weight
        for k in range(len(M)):
            print('k', k)
            for i in range(len(M)):
                print('i', i)
                for j in range(len(M)):
                    newDistance = M[i][k] + M[k][j]
                    if newDistance < M[i][j]:
                        M[i][j] = newDistance
                        P[i][j] = k
        return M, P