# -*- coding: utf-8 -*-
import numpy as np

from keras import backend as K
from keras import regularizers
import inspect
import tensorflow as tf
from keras.layers import InputSpec, Layer, Dense, Conv2D, Dropout, SeparableConv2D
# from binarize.custom_layers import Conv2D
from keras import constraints
from keras import initializers

from binarize.binary_ops import binarize

# from binary_ops import log_quantize as quantize
# from binary_ops import quantize as quantize


class DropoutNoScale(Dropout):
    '''Keras Dropout does scale the input in training phase, which is undesirable here.
    '''
    def call(self, inputs, training=None):
        if 0. < self.rate < 1.:
            noise_shape = self._get_noise_shape(inputs)

            def dropped_inputs():
                return K.dropout(inputs, self.rate, noise_shape,
                                 seed=self.seed) * (1 - self.rate)
            return K.in_train_phase(dropped_inputs, inputs,
                                    training=training)
        return inputs


class Clip(constraints.Constraint):
    def __init__(self, min_value, max_value=None):
        self.min_value = min_value
        self.max_value = max_value
        if not self.max_value:
            self.max_value = -self.min_value
        if self.min_value > self.max_value:
            self.min_value, self.max_value = self.max_value, self.min_value

    def __call__(self, p):
        return K.clip(p, self.min_value, self.max_value)

    def get_config(self):
        return {"min_value": self.min_value,
                "max_value": self.max_value}


class BinaryDense(Dense):
    ''' Binarized Dense layer
    References: 
    "BinaryNet: Training Deep Neural Networks with Weights and Activations Constrained to +1 or -1" [http://arxiv.org/abs/1602.02830]
    '''
    def __init__(self, units, H=1., kernel_lr_multiplier='Glorot', bias_lr_multiplier=None, w_getter = None, **kwargs):
        super(BinaryDense, self).__init__(units, **kwargs)
        self.H = H
        self.kernel_lr_multiplier = kernel_lr_multiplier
        self.bias_lr_multiplier = bias_lr_multiplier
        self.w_getter = w_getter
        super(BinaryDense, self).__init__(units, **kwargs)
    
    def build(self, input_shape):
        assert len(input_shape) >= 2
        input_dim = input_shape[1]

        if self.H == 'Glorot':
            self.H = np.float32(np.sqrt(1.5 / (input_dim + self.units)))
            #print('Glorot H: {}'.format(self.H))
        if self.kernel_lr_multiplier == 'Glorot':
            self.kernel_lr_multiplier = np.float32(1. / np.sqrt(1.5 / (input_dim + self.units)))
            #print('Glorot learning rate multiplier: {}'.format(self.kernel_lr_multiplier))
            
        self.kernel_constraint = Clip(-self.H, self.H)
        self.kernel_initializer = initializers.RandomUniform(-self.H, self.H)
        # self.kernel_regularizer = regularizers.l2(0.000001)
        kwargs = {'set_weight' : self.w_getter}
        print("Printing inspect: ",inspect.getargspec(self.add_weight))

        self.kernel = self.add_weight(shape=(input_dim, self.units),
                                     initializer=self.kernel_initializer,
                                     name='kernel',
                                     regularizer=self.kernel_regularizer,
                                     constraint=self.kernel_constraint,
                                     **kwargs)

        if self.use_bias:
            # self.lr_multipliers = [self.kernel_lr_multiplier, self.bias_lr_multiplier]
            self.bias = self.add_weight(shape=(self.output_dim,),
                                     initializer=self.bias_initializer,
                                     name='bias',
                                     regularizer=self.bias_regularizer,
                                     constraint=self.bias_constraint)
        else:
            # self.lr_multipliers = [self.kernel_lr_multiplier]
            self.bias = None

        self.input_spec = InputSpec(min_ndim=2, axes={-1: input_dim})
        self.built = True


    def call(self, inputs):
        self.kernel = tf.Print(self.kernel, [self.kernel], "----- Dense kernel-----", first_n = 3, summarize = 20)
        binary_kernel = binarize(self.kernel, H=self.H)
        binary_kernel = tf.Print(binary_kernel, [binary_kernel], "----- Dense binarize kernel-----", first_n = 3, summarize = 20)
        output = K.dot(inputs, binary_kernel)
        if self.use_bias:
            output = K.bias_add(output, self.bias)
        if self.activation is not None:
            output = self.activation(output)
        return output
        
    def get_config(self):
        config = {'H': self.H,
                  'kernel_lr_multiplier': self.kernel_lr_multiplier,
                  'bias_lr_multiplier': self.bias_lr_multiplier}
        base_config = super(BinaryDense, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class BinaryConv2D(Conv2D):
    '''Binarized Convolution2D layer
    References: 
    "BinaryNet: Training Deep Neural Networks with Weights and Activations Constrained to +1 or -1" [http://arxiv.org/abs/1602.02830]
    '''
    def __init__(self, filters,strides, kernel_lr_multiplier='Glorot', 
                 bias_lr_multiplier=None, H=1., binarize = True, w_getter = None,  **kwargs):
        super(BinaryConv2D, self).__init__(filters, **kwargs)
        self.H = H
        self.kernel_lr_multiplier = kernel_lr_multiplier
        self.bias_lr_multiplier = bias_lr_multiplier
        self.strides = strides
        self.binarize = binarize
        self.w_getter = w_getter

        
    def build(self, input_shape):
        if self.data_format == 'channels_first':
            channel_axis = 1
        else:
            channel_axis = -1 
        # print("Input shape: ", input_shape)
        # print("w_getter: ", self.w_getter)
        # print("self.kernel_size: ", self.kernel_size)
        # print("self.filters: ", self.filters)

        # # Add the input_shape calculation to the graph :) 
        # input_dim_tf = tf.slice(tf.shape(self.w_getter), [0],  [channel_axis])
        # self.filters_tf = tf.convert_to_tensor(self.filters)
        # kernel_shape_tf = tf.concat([tf.convert_to_tensor(self.kernel_size), input_dim_tf, self.filters_tf], 0)
        # base_tf = tf.convert_to_tensor(self.kernel_size[0] * self.kernel_size[1])
        # nb_input_tf = tf.multiply(input_dim_tf, base_tf)
        # nb_output_tf = tf.multiply(self.filters_tf, base_tf)
        
        
        # if input_shape[channel_axis] is None:
        #         raise ValueError('The channel dimension of the inputs '
        #                          'should be defined. Found `None`.')
        
        input_dim = input_shape[channel_axis]
        kernel_shape = self.kernel_size + (input_dim, self.filters)
        # print("input_dim: ", input_dim)
        # print("kernel_shape: ", kernel_shape)

            
        base = self.kernel_size[0] * self.kernel_size[1]

        # Always false hence not creating the graph! 
        # if self.H == 'Glorot':
            # nb_input = int(input_dim * base)
            # nb_output = int(self.filters * base)
            # self.H = np.float32(np.sqrt(1.5 / (nb_input + nb_output)))
            #print('Glorot H: {}'.format(self.H))
        
        # Creating the graph 
        # if self.kernel_lr_multiplier == 'Glorot':
            # nb_input = int(input_dim * base)
            # nb_output = int(self.filters * base)
            # self.kernel_lr_multiplier = np.float32(1. / np.sqrt(1.5/ (nb_input + nb_output)))
            #print('Glorot learning rate multiplier: {}'.format(self.lr_multiplier))

        self.kernel_constraint = Clip(-self.H, self.H)
        self.kernel_initializer = initializers.RandomUniform(-self.H, self.H)
        # self.kernel_regularizer = regularizers.l2(0.000001)
        # print("Printing inspect: ",inspect.signature(self.add_weight))

        kwargs = {'set_weight' : self.w_getter}

        self.kernel = self.add_weight(shape=kernel_shape,
                                 initializer=self.kernel_initializer,
                                 name='kernel',
                                 regularizer=self.kernel_regularizer,
                                 constraint=self.kernel_constraint,
                                 **kwargs)

        if self.use_bias:
            # self.lr_multipliers = [self.kernel_lr_multiplier, self.bias_lr_multiplier]
            self.bias = self.add_weight((self.output_dim,),
                                     initializer=self.bias_initializers,
                                     name='bias',
                                     regularizer=self.bias_regularizer,
                                     constraint=self.bias_constraint)

        else:
            # self.lr_multipliers = [self.kernel_lr_multiplier]
            self.bias = None

        # Set input spec.
        self.input_spec = InputSpec(ndim=4, axes={channel_axis: input_dim})
        self.built = True

    def call(self, inputs):
        self.kernel = tf.Print(self.kernel, [self.kernel], "----- Conv2d kernel-----", first_n = 3, summarize = 20)

        if(self.binarize is True):
            # print('-------------Will Binarize-------------')
            binary_kernel = binarize(self.kernel, H=self.H) 
        else:
            # print('-------------Wont Binarize-------------')
            binary_kernel = self.kernel
        binary_kernel = tf.Print(binary_kernel, [binary_kernel], "----- Conv2d Binarize kernel-----", first_n = 3, summarize = 20)

        outputs = K.conv2d(
            inputs,
            binary_kernel,
            strides=self.strides,
            padding=self.padding,
            data_format=self.data_format,
            dilation_rate=self.dilation_rate)

        if self.use_bias:
            outputs = K.bias_add(
                outputs,
                self.bias,
                data_format=self.data_format)

        if self.activation is not None:
            return self.activation(outputs)
        return outputs
        
    def get_config(self):
        config = {'H': self.H,
                  'kernel_lr_multiplier': self.kernel_lr_multiplier,
                  'bias_lr_multiplier': self.bias_lr_multiplier}
        base_config = super(BinaryConv2D, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class DepthwiseBinaryConv2D(SeparableConv2D):
    '''Binarized Convolution2D layer
    References: 
    "BinaryNet: Training Deep Neural Networks with Weights and Activations Constrained to +1 or -1" [http://arxiv.org/abs/1602.02830]
    '''
    def __init__(self, filters,strides, kernel_lr_multiplier='Glorot', 
                 bias_lr_multiplier=None, H=1., w_getter = None, **kwargs):
        super(DepthwiseBinaryConv2D, self).__init__(filters, **kwargs)
        self.H = H
        self.kernel_lr_multiplier = kernel_lr_multiplier
        self.bias_lr_multiplier = bias_lr_multiplier
        self.strides = strides
        self.depthwise_mul = kwargs.get('depth_multiplier',1)
        self.w_getter = w_getter
        
    def build(self, input_shape):
        if self.data_format == 'channels_first':
            channel_axis = 1
        else:
            channel_axis = -1 
        if input_shape[channel_axis] is None:
                raise ValueError('The channel dimension of the inputs '
                                 'should be defined. Found `None`.')

        input_dim = input_shape[channel_axis]
        depthwise_kernel_shape = self.kernel_size + (input_dim, self.depthwise_mul)
        pointwise_kernel_shape = [1,1,input_dim*self.depthwise_mul, self.filters]

        base = self.kernel_size[0] * self.kernel_size[1]
        if self.H == 'Glorot':
            nb_input = int(input_dim * base)
            nb_output = int(self.filters * base)
            self.H = np.float32(np.sqrt(1.5 / (nb_input + nb_output)))
            #print('Glorot H: {}'.format(self.H))
            
        if self.kernel_lr_multiplier == 'Glorot':
            nb_input = int(input_dim * base)
            nb_output = int(self.filters * base)
            self.kernel_lr_multiplier = np.float32(1. / np.sqrt(1.5/ (nb_input + nb_output)))
            #print('Glorot learning rate multiplier: {}'.format(self.lr_multiplier))

        self.kernel_constraint = Clip(-self.H, self.H)
        self.kernel_initializer = initializers.RandomUniform(-self.H, self.H)
        # self.kernel_regularizer = regularizers.l2(0.000001)

        kwargs = {'set_weight' : self.w_getter[0]}
        self.depthwise_kernel = self.add_weight(shape=depthwise_kernel_shape,
                                 initializer=self.kernel_initializer,
                                 name='kernel',
                                 regularizer=self.kernel_regularizer,
                                 constraint=self.kernel_constraint, **kwargs)

        kwargs = {'set_weight' : self.w_getter[1]}
        self.pointwise_kernel = self.add_weight(shape=pointwise_kernel_shape,
                                 initializer=self.kernel_initializer,
                                 name='kernel',
                                 regularizer=self.kernel_regularizer,
                                 constraint=self.kernel_constraint, **kwargs)

        if self.use_bias:
            # self.lr_multipliers = [self.kernel_lr_multiplier, self.bias_lr_multiplier]
            self.bias = self.add_weight((self.output_dim,),
                                     initializer=self.bias_initializers,
                                     name='bias',
                                     regularizer=self.bias_regularizer,
                                     constraint=self.bias_constraint)

        else:
            # self.lr_multipliers = [self.kernel_lr_multiplier]
            self.bias = None

        # Set input spec.
        self.input_spec = InputSpec(ndim=4, axes={channel_axis: input_dim})
        self.built = True

    def call(self, inputs):
        self.depthwise_kernel = tf.Print(self.depthwise_kernel, [self.depthwise_kernel], "----- Conv2d-dep kernel-----", first_n = 3, summarize = 20)
        self.pointwise_kernel = tf.Print(self.pointwise_kernel, [self.pointwise_kernel], "----- Conv2d-point kernel-----", first_n = 3, summarize = 20)
        
        depthwise_binary_kernel = binarize(self.depthwise_kernel, H=self.H)
        pointwise_binary_kernel = binarize(self.pointwise_kernel, H=self.H) 

        outputs = K.separable_conv2d(
            inputs,
            depthwise_binary_kernel,
            pointwise_binary_kernel,
            strides=self.strides,
            padding=self.padding,
            data_format=self.data_format,
            dilation_rate=self.dilation_rate)

        depthwise_binary_kernel = tf.Print(depthwise_binary_kernel, [depthwise_binary_kernel], "----- Conv2d-dep binarize kernel-----", first_n = 3, summarize = 20)
        pointwise_binary_kernel = tf.Print(pointwise_binary_kernel, [pointwise_binary_kernel], "----- Conv2d-point binarize kernel-----", first_n = 3, summarize = 20)

        if self.use_bias:
            outputs = K.bias_add(
                outputs,
                self.bias,
                data_format=self.data_format)

        if self.activation is not None:
            return self.activation(outputs)
        return outputs
        
    def get_config(self):
        config = {'H': self.H,
                  'kernel_lr_multiplier': self.kernel_lr_multiplier,
                  'bias_lr_multiplier': self.bias_lr_multiplier}
        base_config = super(BinaryConv2D, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


# Aliases

BinaryConvolution2D = BinaryConv2D
