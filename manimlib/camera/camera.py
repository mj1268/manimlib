from __future__ import annotations

import itertools as it

import moderngl
import numpy as np
import OpenGL.GL as gl
from PIL import Image

from manimlib.camera.camera_frame import CameraFrame
from manimlib.constants import BLACK
from manimlib.constants import DEFAULT_FPS
from manimlib.constants import DEFAULT_PIXEL_HEIGHT, DEFAULT_PIXEL_WIDTH
from manimlib.constants import FRAME_WIDTH
from manimlib.mobject.mobject import Mobject
from manimlib.mobject.mobject import Point
from manimlib.utils.color import color_to_rgba
from manimlib.utils.shaders import get_texture_id

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from manimlib.shader_wrapper import ShaderWrapper
    from manimlib.typing import ManimColor, Vect3
    from manimlib.window import Window
    from typing import Any, Iterable


class Camera(object):
    def __init__(
        self,
        window: Window | None = None,
        background_image: str | None = None,
        frame_config: dict = dict(),
        pixel_width: int = DEFAULT_PIXEL_WIDTH,
        pixel_height: int = DEFAULT_PIXEL_HEIGHT,
        fps: int = DEFAULT_FPS,
        # Note: frame height and width will be resized to match the pixel aspect ratio
        background_color: ManimColor = BLACK,
        background_opacity: float = 1.0,
        # Points in vectorized mobjects with norm greater
        # than this value will be rescaled.
        max_allowable_norm: float = FRAME_WIDTH,
        image_mode: str = "RGBA",
        n_channels: int = 4,
        pixel_array_dtype: type = np.uint8,
        light_source_position: Vect3 = np.array([-10, 10, 10]),
        # Although vector graphics handle antialiasing fine
        # without multisampling, for 3d scenes one might want
        # to set samples to be greater than 0.
        samples: int = 0,
    ):
        self.background_image = background_image
        self.window = window
        self.default_pixel_shape = (pixel_width, pixel_height)
        self.fps = fps
        self.max_allowable_norm = max_allowable_norm
        self.image_mode = image_mode
        self.n_channels = n_channels
        self.pixel_array_dtype = pixel_array_dtype
        self.light_source_position = light_source_position
        self.samples = samples

        self.rgb_max_val: float = np.iinfo(self.pixel_array_dtype).max
        self.background_rgba: list[float] = list(color_to_rgba(
            background_color, background_opacity
        ))
        self.perspective_uniforms = dict()
        self.init_frame(**frame_config)
        self.init_context(window)
        self.init_light_source()
        self.refresh_perspective_uniforms()
        # A cached map from mobjects to their associated list of render groups
        # so that these render groups are not regenerated unnecessarily for static
        # mobjects
        self.mob_to_render_groups = {}

    def init_frame(self, **config) -> None:
        self.frame = CameraFrame(**config)

    def init_context(self, window: Window | None = None) -> None:
        if window is None:
            self.ctx = moderngl.create_standalone_context()
            self.fbo = self.get_fbo(self.samples)
        else:
            self.ctx = window.ctx
            self.fbo = self.ctx.detect_framebuffer()
        self.fbo.use()

        self.ctx.enable(moderngl.PROGRAM_POINT_SIZE)
        self.ctx.enable(moderngl.BLEND)

        # This is the frame buffer we'll draw into when emitting frames
        self.draw_fbo = self.get_fbo(samples=0)

    def init_light_source(self) -> None:
        self.light_source = Point(self.light_source_position)

    # Methods associated with the frame buffer
    def get_fbo(
        self,
        samples: int = 0
    ) -> moderngl.Framebuffer:
        return self.ctx.framebuffer(
            color_attachments=self.ctx.texture(
                self.default_pixel_shape,
                components=self.n_channels,
                samples=samples,
            ),
            depth_attachment=self.ctx.depth_renderbuffer(
                self.default_pixel_shape,
                samples=samples
            )
        )

    def clear(self) -> None:
        self.fbo.clear(*self.background_rgba)

    def get_raw_fbo_data(self, dtype: str = 'f1') -> bytes:
        # Copy blocks from fbo into draw_fbo using Blit
        gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, self.fbo.glo)
        gl.glBindFramebuffer(gl.GL_DRAW_FRAMEBUFFER, self.draw_fbo.glo)
        if self.window is not None:
            src_viewport = self.window.viewport
        else:
            src_viewport = self.fbo.viewport
        gl.glBlitFramebuffer(
            *src_viewport,
            *self.draw_fbo.viewport,
            gl.GL_COLOR_BUFFER_BIT, gl.GL_LINEAR
        )
        return self.draw_fbo.read(
            viewport=self.draw_fbo.viewport,
            components=self.n_channels,
            dtype=dtype,
        )

    def get_image(self) -> Image.Image:
        return Image.frombytes(
            'RGBA',
            self.get_pixel_shape(),
            self.get_raw_fbo_data(),
            'raw', 'RGBA', 0, -1
        )

    def get_pixel_array(self) -> np.ndarray:
        raw = self.get_raw_fbo_data(dtype='f4')
        flat_arr = np.frombuffer(raw, dtype='f4')
        arr = flat_arr.reshape([*reversed(self.draw_fbo.size), self.n_channels])
        arr = arr[::-1]
        # Convert from float
        return (self.rgb_max_val * arr).astype(self.pixel_array_dtype)

    # Needed?
    def get_texture(self) -> moderngl.Texture:
        texture = self.ctx.texture(
            size=self.fbo.size,
            components=4,
            data=self.get_raw_fbo_data(),
            dtype='f4'
        )
        return texture

    # Getting camera attributes
    def get_pixel_size(self) -> float:
        return self.frame.get_shape()[0] / self.get_pixel_shape()[0]

    def get_pixel_shape(self) -> tuple[int, int]:
        return self.draw_fbo.size

    def get_pixel_width(self) -> int:
        return self.get_pixel_shape()[0]

    def get_pixel_height(self) -> int:
        return self.get_pixel_shape()[1]

    def get_aspect_ratio(self):
        pw, ph = self.get_pixel_shape()
        return pw / ph

    def get_frame_height(self) -> float:
        return self.frame.get_height()

    def get_frame_width(self) -> float:
        return self.frame.get_width()

    def get_frame_shape(self) -> tuple[float, float]:
        return (self.get_frame_width(), self.get_frame_height())

    def get_frame_center(self) -> np.ndarray:
        return self.frame.get_center()

    def get_location(self) -> tuple[float, float, float]:
        return self.frame.get_implied_camera_location()

    def resize_frame_shape(self, fixed_dimension: bool = False) -> None:
        """
        Changes frame_shape to match the aspect ratio
        of the pixels, where fixed_dimension determines
        whether frame_height or frame_width
        remains fixed while the other changes accordingly.
        """
        frame_height = self.get_frame_height()
        frame_width = self.get_frame_width()
        aspect_ratio = self.get_aspect_ratio()
        if not fixed_dimension:
            frame_height = frame_width / aspect_ratio
        else:
            frame_width = aspect_ratio * frame_height
        self.frame.set_height(frame_height, stretch=true)
        self.frame.set_width(frame_width, stretch=true)

    # Rendering
    def capture(self, *mobjects: Mobject) -> None:
        self.refresh_perspective_uniforms()
        for mobject in mobjects:
            for render_group in self.get_render_group_list(mobject):
                self.render(render_group)

    def render(self, render_group: dict[str, Any]) -> None:
        shader_wrapper = render_group["shader_wrapper"]
        shader_wrapper.render(self.perspective_uniforms)

        if render_group["single_use"]:
            self.release_render_group(render_group)

    def get_render_group_list(self, mobject: Mobject) -> Iterable[dict[str, Any]]:
        if mobject.is_changing():
            return self.generate_render_group_list(mobject)

        # Otherwise, cache result for later use
        key = id(mobject)
        if key not in self.mob_to_render_groups:
            self.mob_to_render_groups[key] = list(self.generate_render_group_list(mobject))
        return self.mob_to_render_groups[key]

    def generate_render_group_list(self, mobject: Mobject) -> Iterable[dict[str, Any]]:
        return (
            self.get_render_group(sw, single_use=mobject.is_changing())
            for sw in mobject.get_shader_wrapper_list(self.ctx)
        )

    def get_render_group(
        self,
        shader_wrapper: ShaderWrapper,
        single_use: bool = True
    ) -> dict[str, Any]:
        shader_wrapper.get_vao()
        return {
            "shader_wrapper": shader_wrapper,
            "single_use": single_use,
        }

    def release_render_group(self, render_group: dict[str, Any]) -> None:
        render_group["shader_wrapper"].release()

    def refresh_static_mobjects(self) -> None:
        for render_group in it.chain(*self.mob_to_render_groups.values()):
            self.release_render_group(render_group)
        self.mob_to_render_groups = {}

    def refresh_perspective_uniforms(self) -> None:
        frame = self.frame
        view_matrix = frame.get_view_matrix()
        light_pos = self.light_source.get_location()
        cam_pos = self.frame.get_implied_camera_location()

        self.perspective_uniforms.update(
            frame_shape=frame.get_shape(),
            pixel_size=self.get_pixel_size(),
            view=tuple(view_matrix.T.flatten()),
            camera_position=tuple(cam_pos),
            light_position=tuple(light_pos),
            focal_distance=frame.get_focal_distance(),
        )


# Mostly just defined so old scenes don't break
class ThreeDCamera(Camera):
    def __init__(self, samples: int = 4, **kwargs):
        super().__init__(samples=samples, **kwargs)
