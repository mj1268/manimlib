from __future__ import annotations

import moderngl
import numpy as np

from manimlib.constants import GREY
from manimlib.constants import OUT
from manimlib.mobject.mobject import Mobject
from manimlib.utils.bezier import integer_interpolate
from manimlib.utils.bezier import interpolate
from manimlib.utils.images import get_full_raster_image_path
from manimlib.utils.iterables import listify
from manimlib.utils.space_ops import normalize_along_axis

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable, Iterable, Sequence, Tuple

    from manimlib.camera.camera import Camera
    from manimlib.typing import ManimColor, Vect3, Vect3Array


class Surface(Mobject):
    render_primitive: int = moderngl.TRIANGLES
    shader_folder: str = "surface"
    shader_dtype: Sequence[Tuple[str, type, Tuple[int]]] = [
        ('point', np.float32, (3,)),
        ('du_point', np.float32, (3,)),
        ('dv_point', np.float32, (3,)),
        ('color', np.float32, (4,)),
    ]

    def __init__(
        self,
        color: ManimColor = GREY,
        reflectiveness: float = 0.3,
        gloss: float = 0.1,
        shadow: float = 0.4,
        depth_test: bool = True,
        u_range: Tuple[float, float] = (0.0, 1.0),
        v_range: Tuple[float, float] = (0.0, 1.0),
        # Resolution counts number of points sampled, which for
        # each coordinate is one more than the the number of
        # rows/columns of approximating squares
        resolution: Tuple[int, int] = (101, 101),
        prefered_creation_axis: int = 1,
        # For du and dv steps.  Much smaller and numerical error
        # can crop up in the shaders.
        epsilon: float = 1e-5,
        **kwargs
    ):
        self.u_range = u_range
        self.v_range = v_range
        self.resolution = resolution
        self.prefered_creation_axis = prefered_creation_axis
        self.epsilon = epsilon

        super().__init__(
            **kwargs,
            color=color,
            reflectiveness=reflectiveness,
            gloss=gloss,
            shadow=shadow,
            depth_test=depth_test,
        )
        self.compute_triangle_indices()

    def init_uniforms(self):
        super().init_uniforms()
        self.uniforms["clip_plane"] = np.zeros(4)

    def uv_func(self, u: float, v: float) -> tuple[float, float, float]:
        # To be implemented in subclasses
        return (u, v, 0.0)

    def init_points(self):
        dim = self.dim
        nu, nv = self.resolution
        u_range = np.linspace(*self.u_range, nu)
        v_range = np.linspace(*self.v_range, nv)

        # Get three lists:
        # - Points generated by pure uv values
        # - Those generated by values nudged by du
        # - Those generated by values nudged by dv
        point_lists = []
        for (du, dv) in [(0, 0), (self.epsilon, 0), (0, self.epsilon)]:
            uv_grid = np.array([[[u + du, v + dv] for v in v_range] for u in u_range])
            point_grid = np.apply_along_axis(lambda p: self.uv_func(*p), 2, uv_grid)
            point_lists.append(point_grid.reshape((nu * nv, dim)))
        # Rather than tracking normal vectors, the points list will hold on to the
        # infinitesimal nudged values alongside the original values.  This way, one
        # can perform all the manipulations they'd like to the surface, and normals
        # are still easily recoverable.
        self.set_points(np.vstack(point_lists))

    def compute_triangle_indices(self):
        # TODO, if there is an event which changes
        # the resolution of the surface, make sure
        # this is called.
        nu, nv = self.resolution
        if nu == 0 or nv == 0:
            self.triangle_indices = np.zeros(0, dtype=int)
            return
        index_grid = np.arange(nu * nv).reshape((nu, nv))
        indices = np.zeros(6 * (nu - 1) * (nv - 1), dtype=int)
        indices[0::6] = index_grid[:-1, :-1].flatten()  # Top left
        indices[1::6] = index_grid[+1:, :-1].flatten()  # Bottom left
        indices[2::6] = index_grid[:-1, +1:].flatten()  # Top right
        indices[3::6] = index_grid[:-1, +1:].flatten()  # Top right
        indices[4::6] = index_grid[+1:, :-1].flatten()  # Bottom left
        indices[5::6] = index_grid[+1:, +1:].flatten()  # Bottom right
        self.triangle_indices = indices

    def get_triangle_indices(self) -> np.ndarray:
        return self.triangle_indices

    def get_surface_points_and_nudged_points(
        self
    ) -> tuple[Vect3Array, Vect3Array, Vect3Array]:
        points = self.get_points()
        k = len(points) // 3
        return points[:k], points[k:2 * k], points[2 * k:]

    def get_unit_normals(self) -> Vect3Array:
        s_points, du_points, dv_points = self.get_surface_points_and_nudged_points()
        normals = np.cross(
            (du_points - s_points) / self.epsilon,
            (dv_points - s_points) / self.epsilon,
        )
        return normalize_along_axis(normals, 1)

    def pointwise_become_partial(
        self,
        smobject: "Surface",
        a: float,
        b: float,
        axis: int | None = None
    ):
        assert(isinstance(smobject, Surface))
        if axis is None:
            axis = self.prefered_creation_axis
        if a <= 0 and b >= 1:
            self.match_points(smobject)
            return self

        nu, nv = smobject.resolution
        self.set_points(np.vstack([
            self.get_partial_points_array(arr.copy(), a, b, (nu, nv, 3), axis=axis)
            for arr in smobject.get_surface_points_and_nudged_points()
        ]))
        return self

    def get_partial_points_array(
        self,
        points: Vect3Array,
        a: float,
        b: float,
        resolution: Sequence[int],
        axis: int
    ) -> Vect3Array:
        if len(points) == 0:
            return points
        nu, nv = resolution[:2]
        points = points.reshape(resolution)
        max_index = resolution[axis] - 1
        lower_index, lower_residue = integer_interpolate(0, max_index, a)
        upper_index, upper_residue = integer_interpolate(0, max_index, b)
        if axis == 0:
            points[:lower_index] = interpolate(
                points[lower_index],
                points[lower_index + 1],
                lower_residue
            )
            points[upper_index + 1:] = interpolate(
                points[upper_index],
                points[upper_index + 1],
                upper_residue
            )
        else:
            shape = (nu, 1, resolution[2])
            points[:, :lower_index] = interpolate(
                points[:, lower_index],
                points[:, lower_index + 1],
                lower_residue
            ).reshape(shape)
            points[:, upper_index + 1:] = interpolate(
                points[:, upper_index],
                points[:, upper_index + 1],
                upper_residue
            ).reshape(shape)
        return points.reshape((nu * nv, *resolution[2:]))

    def sort_faces_back_to_front(self, vect: Vect3 = OUT):
        tri_is = self.triangle_indices
        points = self.get_points()

        dots = (points[tri_is[::3]] * vect).sum(1)
        indices = np.argsort(dots)
        for k in range(3):
            tri_is[k::3] = tri_is[k::3][indices]
        return self

    def always_sort_to_camera(self, camera: Camera):
        def updater(surface: Surface):
            vect = camera.get_location() - surface.get_center()
            surface.sort_faces_back_to_front(vect)
        self.add_updater(updater)

    def set_clip_plane(
        self,
        vect: Vect3 | None = None,
        threshold: float | None = None
    ):
        if vect is not None:
            self.uniforms["clip_plane"][:3] = vect
        if threshold is not None:
            self.uniforms["clip_plane"][3] = threshold
        self.shader_wrapper.use_clip_plane = True
        return self

    def deactivate_clip_plane(self):
        self.uniforms["clip_plane"][:] = 0
        self.shader_wrapper.use_clip_plane = False
        return self

    # For shaders
    def get_shader_data(self) -> np.ndarray:
        s_points, du_points, dv_points = self.get_surface_points_and_nudged_points()
        shader_data = self.get_resized_shader_data_array(len(s_points))
        if "points" not in self.locked_data_keys:
            shader_data["point"] = s_points
            shader_data["du_point"] = du_points
            shader_data["dv_point"] = dv_points
        self.fill_in_shader_color_info(shader_data)
        return shader_data

    def fill_in_shader_color_info(self, shader_data: np.ndarray) -> np.ndarray:
        self.read_data_to_shader(shader_data, "color", "rgbas")
        return shader_data

    def get_shader_vert_indices(self) -> np.ndarray:
        return self.get_triangle_indices()


class ParametricSurface(Surface):
    def __init__(
        self,
        uv_func: Callable[[float, float], Iterable[float]],
        u_range: tuple[float, float] = (0, 1),
        v_range: tuple[float, float] = (0, 1),
        **kwargs
    ):
        self.passed_uv_func = uv_func
        super().__init__(u_range=u_range, v_range=v_range, **kwargs)

    def uv_func(self, u, v):
        return self.passed_uv_func(u, v)


class SGroup(Surface):
    def __init__(
        self,
        *parametric_surfaces: Surface,
        **kwargs
    ):
        super().__init__(uv_func=None, resolution=(0, 0), **kwargs)
        self.add(*parametric_surfaces)

    def init_points(self):
        pass  # Needed?


class TexturedSurface(Surface):
    shader_folder: str = "textured_surface"
    shader_dtype: Sequence[Tuple[str, type, Tuple[int]]] = [
        ('point', np.float32, (3,)),
        ('du_point', np.float32, (3,)),
        ('dv_point', np.float32, (3,)),
        ('im_coords', np.float32, (2,)),
        ('opacity', np.float32, (1,)),
    ]

    def __init__(
        self,
        uv_surface: Surface,
        image_file: str,
        dark_image_file: str | None = None,
        **kwargs
    ):
        if not isinstance(uv_surface, Surface):
            raise Exception("uv_surface must be of type Surface")
        # Set texture information
        if dark_image_file is None:
            dark_image_file = image_file
            self.num_textures = 1
        else:
            self.num_textures = 2

        texture_paths = {
            "LightTexture": get_full_raster_image_path(image_file),
            "DarkTexture": get_full_raster_image_path(dark_image_file),
        }

        self.uv_surface = uv_surface
        self.uv_func = uv_surface.uv_func
        self.u_range: Tuple[float, float] = uv_surface.u_range
        self.v_range: Tuple[float, float] = uv_surface.v_range
        self.resolution: Tuple[int, int] = uv_surface.resolution
        super().__init__(
            texture_paths=texture_paths,
            gloss=uv_surface.gloss,
            **kwargs
        )

    def init_data(self):
        super().init_data()
        self.data["im_coords"] = np.zeros((0, 2))
        self.data["opacity"] = np.zeros((0, 1))

    def init_points(self):
        nu, nv = self.uv_surface.resolution
        self.set_points(self.uv_surface.get_points())
        self.data["im_coords"] = np.array([
            [u, v]
            for u in np.linspace(0, 1, nu)
            for v in np.linspace(1, 0, nv)  # Reverse y-direction
        ])

    def init_uniforms(self):
        super().init_uniforms()
        self.uniforms["num_textures"] = self.num_textures

    def init_colors(self):
        self.data["opacity"] = np.array([self.uv_surface.data["rgbas"][:, 3]])

    def set_opacity(self, opacity: float, recurse: bool = True):
        for mob in self.get_family(recurse):
            mob.data["opacity"] = np.array([[o] for o in listify(opacity)])
        return self

    def pointwise_become_partial(
        self,
        tsmobject: "TexturedSurface",
        a: float,
        b: float,
        axis: int = 1
    ):
        super().pointwise_become_partial(tsmobject, a, b, axis)
        im_coords = self.data["im_coords"]
        im_coords[:] = tsmobject.data["im_coords"]
        if a <= 0 and b >= 1:
            return self
        nu, nv = tsmobject.resolution
        im_coords[:] = self.get_partial_points_array(
            im_coords, a, b, (nu, nv, 2), axis
        )
        return self

    def fill_in_shader_color_info(self, shader_data: np.ndarray) -> np.ndarray:
        self.read_data_to_shader(shader_data, "opacity", "opacity")
        self.read_data_to_shader(shader_data, "im_coords", "im_coords")
        return shader_data
