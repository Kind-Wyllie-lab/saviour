import numpy as np
from OpenGL.GL import glGenTextures, glTexImage2D
from OpenGL.raw.GL.ARB.internalformat_query2 import GL_TEXTURE_2D
from OpenGL.raw.GL.VERSION.GL_1_0 import glTexParameteri, GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_TEXTURE_MIN_FILTER, \
    GL_LINEAR, GL_TEXTURE_MAG_FILTER, GL_RGBA, glViewport
from OpenGL.raw.GL.VERSION.GL_1_1 import glBindTexture
from OpenGL.raw.GL.VERSION.GL_1_2 import GL_CLAMP_TO_EDGE
from OpenGL.raw.GL.VERSION.GL_3_0 import glGenerateMipmap
from OpenGL.raw.GL._types import GL_UNSIGNED_BYTE
from PIL import Image

def vcalc(x0, x1, t, dim):
    """Calculate required velocity of image"""
    v = (x1[dim] - x0[dim]) / t
    return v


def load_texture(path, angle):
    """Load texture from file"""
    # Load image using PIL
    image = Image.open(path)
    image = image.transpose(Image.FLIP_TOP_BOTTOM)
    image = image.rotate(angle, expand=True, resample=Image.BICUBIC)
    img_data = image.convert("RGBA").tobytes()
    width, height = image.size

    # Generate texture
    texture = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, texture)

    # Set texture parameters
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)

    # Upload texture data
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, img_data)
    glGenerateMipmap(GL_TEXTURE_2D)

    return texture


def translate_matrix(x, y, z):
    return np.array([
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [x, y, z, 1.0]
    ], dtype=np.float32)


def scale_matrix(s):
    return np.array([
        [s, 0.0, 0.0, 0.0],
        [0.0, s, 0.0, 0.0],
        [0.0, 0.0, s, 0.0],
        [0.0, 0.0, 0.0, 1.0]
    ], dtype=np.float32)


def scale_matrix_seperate(x, y):
    return np.array([
        [x, 0.0, 0.0, 0.0],
        [0.0, y, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0]
    ], dtype=np.float32)


def framebuffer_size_callback(window, width, height):
    """Callback for window reizing"""
    glViewport(0, 0, width, height)
    global width2, height2
    width2 = width
    height2 = height
