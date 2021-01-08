#version 330

uniform vec2 frame_shape;
uniform mat4 to_screen_space;
uniform float is_fixed_in_frame;
uniform float focal_distance;
uniform vec3 light_source_position;

in vec3 point;
in vec3 du_point;
in vec3 dv_point;
in vec4 color;

out vec3 xyz_coords;
out vec3 v_normal;
out vec4 v_color;

#INSERT position_point_into_frame.glsl
#INSERT get_gl_Position.glsl
#INSERT get_rotated_surface_unit_normal_vector.glsl

void main(){
    xyz_coords = position_point_into_frame(point);
    v_normal = get_rotated_surface_unit_normal_vector(point, du_point, dv_point);
    v_color = color;
    gl_Position = get_gl_Position(xyz_coords);
}