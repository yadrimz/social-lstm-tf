'''
Social LSTM model implementation using Tensorflow
Social LSTM Paper: http://vision.stanford.edu/pdf/CVPR16_N_LSTM.pdf

Author : Anirudh Vemula
'''

import tensorflow as tf
import numpy as np
from tensorflow.python.ops import rnn_cell


# TODO: For now just implementing vanilla LSTM without the social layer
class Model():

    def __init__(self, args, infer=False):
        '''
        Initialisation function for the class Model.
        Params:
        args: Contains arguments required for the Model creation
        '''
        # Store the arguments
        self.args = args

        if infer:
            args.batch_size = 1
            args.seq_length = 1

        # args.rnn_size contains the dimension of the hidden state of the LSTM
        cell = rnn_cell.BasicLSTMCell(args.rnn_size, state_is_tuple=False)

        # Multi-layer RNN construction
        # cell = rnn_cell.MultiRNNCell([cell] * args.num_layers, state_is_tuple=False)
        
        # TODO: (improve) For now, let's use a single layer of LSTM
        # TODO: (improve) Dropout layer can be added here
        self.cell = cell

        # TODO: (resolve) Do we need to use a fixed seq_length?
        
        # Input data contains sequence of (x,y) points
        self.input_data = tf.placeholder(tf.float32, [None, args.seq_length, 2])
        # target data contains sequences of (x,y) points as well
        self.target_data = tf.placeholder(tf.float32, [None, args.seq_length, 2])

        # Learning rate
        self.lr = tf.Variable(args.learning_rate, trainable=False, name="learning_rate")

        # Initial cell state of the LSTM (initialised with zeros)
        self.initial_state = cell.zero_state(batch_size=args.batch_size, dtype=tf.float32)

        # Output size is the set of parameters (mu, sigma, corr)
        output_size = 5  # 2 mu, 2 sigma and 1 corr

        # Embedding
        # with tf.variable_scope("coordinate_embedding"):
        #  The spatial embedding using a ReLU layer
        #  Embed the 2D coordinates into embedding_size dimensions
        #  TODO: (improve) For now assume embedding_size = rnn_size
        #  embedding_w = tf.get_variable("embedding_w", [2, args.embedding_size])
        #  embedding_b = tf.get_variable("embedding_b", [args.embedding_size])

        # Output linear layer
        with tf.variable_scope("rnnlm"):
            output_w = tf.get_variable("output_w", [args.rnn_size, output_size], initializer=tf.truncated_normal_initializer(stddev=0.01), trainable=True)
            output_b = tf.get_variable("output_b", [output_size], initializer=tf.constant_initializer(0.01), trainable=True)

        self.output_b = output_b
        self.output_w = output_w

        # Embed inputs i.e. the ReLU embedding layer
        # embedded_inputs = tf.add(tf.matmul(inputs, embedding_w), embedding_b)
        # embedded_inputs = tf.nn.relu(embedded_inputs)
        # TODO: (improve) Add the embedding layer.

        # Split inputs according to sequences.
        inputs = tf.split(1, args.seq_length, self.input_data)
        # Get a list of 2D tensors. Each of size numPoints x 2
        inputs = [tf.squeeze(input_, [1]) for input_ in inputs]

        outputs, last_state = tf.nn.seq2seq.rnn_decoder(inputs, self.initial_state, cell, loop_function=None, scope="rnnlm")

        output = tf.reshape(tf.concat(1, outputs), [-1, args.rnn_size])

        # Apply the linear layer
        output = tf.nn.xw_plus_b(output, output_w, output_b)
        self.final_state = last_state

        # reshape target data so that it aligns with predictions
        flat_target_data = tf.reshape(self.target_data, [-1, 2])
        [x_data, y_data] = tf.split(1, 2, flat_target_data)

        def tf_2d_normal(x, y, mux, muy, sx, sy, rho):
            # eq 3 in the paper
            # and eq 24 & 25 in Graves (2013)
            normx = tf.sub(x, mux)
            normy = tf.sub(y, muy)
            sxsy = tf.mul(sx, sy)
            z = tf.square(tf.div(normx, sx)) + tf.square(tf.div(normy, sy)) - 2*tf.div(tf.mul(rho, tf.mul(normx, normy)), sxsy)
            negRho = 1 - tf.square(rho)
            result = tf.exp(tf.div(-z, 2*negRho))
            denom = 2 * np.pi * tf.mul(sxsy, tf.sqrt(negRho))
            result = tf.div(result, denom)
            self.result = result
            return result

        # Important difference between loss func of Social LSTM and Graves (2013)
        # is that it is evaluated over all time steps in the latter whereas it is
        # done from t_obs+1 to t_pred in the former
        def get_lossfunc(z_mux, z_muy, z_sx, z_sy, z_corr, x_data, y_data):
            result0 = tf_2d_normal(x_data, y_data, z_mux, z_muy, z_sx, z_sy, z_corr)
            
            epsilon = 1e-20  # For numerical stability purposes
            # TODO: (resolve) I don't think we need this as we don't have the inner
            # summation
            # result1 = tf.reduce_sum(result0, 1, keep_dims=True)
            result1 = -tf.log(tf.maximum(result0, epsilon))  # Numerical stability

            # TODO: For now, implementing loss func over all time-steps
            return tf.reduce_sum(result1)

        def get_coef(output):
            # eq 20 -> 22 of Graves (2013)
            # TODO : (resolve) Does Social LSTM paper do this as well?
            # the paper says otherwise but this is essential as we cannot
            # have negative standard deviation and correlation needs to be between
            # -1 and 1

            z = output
            z_mux, z_muy, z_sx, z_sy, z_corr = tf.split(1, 5, z)

            z_sx = tf.exp(z_sx)
            z_sy = tf.exp(z_sy)
            z_corr = tf.tanh(z_corr)

            return [z_mux, z_muy, z_sx, z_sy, z_corr]

        # Extract the coef from the output of the linear layer
        [o_mux, o_muy, o_sx, o_sy, o_corr] = get_coef(output)
        self.output = output

        self.mux = o_mux
        self.muy = o_muy
        self.sx = o_sx
        self.sy = o_sy
        self.corr = o_corr

        # Compute the loss function
        lossfunc = get_lossfunc(o_mux, o_muy, o_sx, o_sy, o_corr, x_data, y_data)

        # Compute the cost
        self.cost = tf.div(lossfunc, (args.batch_size * args.seq_length))

        # Get trainable_variables
        tvars = tf.trainable_variables()

        # TODO: (resolve) We are clipping the gradients as is usually done in LSTM
        # implementations. Social LSTM paper doesn't mention about this at all
        self.gradients = tf.gradients(self.cost, tvars)
        grads, _ = tf.clip_by_global_norm(self.gradients, args.grad_clip)
        # NOTE: Using RMSprop as suggested by Social LSTM instead of Adam as Graves(2013) does
        optimizer = tf.train.AdamOptimizer(self.lr)

        # Train operator
        self.train_op = optimizer.apply_gradients(zip(grads, tvars))

    def sample(self, sess, traj, num=10):
        '''
        Given an initial trajectory (as a list of tuples of points), predict the future trajectory
        until a few timesteps
        Params:
        sess: Current session of Tensorflow
        traj: List of past trajectory points
        num: Number of time-steps into the future to be predicted
        '''
        def sample_gaussian_2d(mux, muy, sx, sy, rho):
            # Extract mean
            mean = [mux, muy]
            # Extract covariance matrix
            cov = [[sx*sx, rho*sx*sy], [rho*sx*sy, sy*sy]]
            # Sample a point from the multivariate normal distribution
            x = np.random.multivariate_normal(mean, cov, 1)
            return x[0][0], x[0][1]

        # Initial state with zeros
        state = sess.run(self.cell.zero_state(1, tf.float32))

        # Iterate over all the positions seen in the trajectory
        for pos in traj[:-1]:
            # Create the input data tensor
            data = np.zeros((1, 1, 2), dtype=np.float32)
            data[0, 0, 0] = pos[0]  # x
            data[0, 0, 1] = pos[1]  # y

            # Create the feed dict
            feed = {self.input_data: data, self.initial_state: state}
            # Get the final state after processing the current position
            [state] = sess.run([self.final_state], feed)

        ret = traj
        # Last position in the observed trajectory

        last_pos = traj[-1]

        # Construct the input data tensor for the last point
        prev_data = np.zeros((1, 1, 2), dtype=np.float32)
        prev_data[0, 0, 0] = last_pos[0]  # x
        prev_data[0, 0, 1] = last_pos[1]  # y

        for t in range(num):
            # Create the feed dict
            feed = {self.input_data: prev_data, self.initial_state: state}

            # Get the final state and also the coef of the distribution of the next point
            [o_mux, o_muy, o_sx, o_sy, o_corr, state] = sess.run([self.mux, self.muy, self.sx, self.sy, self.corr, self.final_state], feed)

            # Sample the next point from the distribution
            next_x, next_y = sample_gaussian_2d(o_mux[0][0], o_muy[0][0], o_sx[0][0], o_sy[0][0], o_corr[0][0])
            # Append the new point to the trajectory
            # ret.append((next_x, next_y))
            ret = np.vstack((ret, [next_x, next_y]))

            # Set the current sampled position as the last observed position
            prev_data[0, 0, 0] = next_x
            prev_data[0, 0, 1] = next_y

        return ret
