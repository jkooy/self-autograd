""" Architect controls architecture of cell by computing gradients of alphas """
import copy
import torch
import utils
import os
import torch.nn as nn
from config import SearchConfig
from torch.autograd import Variable

config = SearchConfig()

logger = utils.get_logger(os.path.join(config.path, "{}.log".format(config.name)))
config.print_params(logger.info)

class Architect():
    """ Compute gradients of alphas """
    def __init__(self, net, w_momentum, w_weight_decay):
        """
        Args:
            net
            w_momentum: weights momentum
        """
        self.net = net
        self.v_net = copy.deepcopy(net)
        self.w_momentum = w_momentum
        self.w_weight_decay = w_weight_decay

    def virtual_step(self, trn_X, trn_y, xi, w_optim, model, Likelihood, batch_size, step):
        """
        Compute unrolled weight w' (virtual step)

        Step process:
        1) forward
        2) calc loss
        3) compute gradient (by backprop)
        4) update gradient

        Args:
            xi: learning rate for virtual gradient step (same as weights lr)
            w_optim: weights optimizer
        """
        # forward & calc loss
        sloss = self.v_net.loss(trn_X, trn_y) # L_trn(w)
        logger.info("unrolled standard loss = {}".format(sloss)) 
        
        
        dataIndex = len(trn_y)+step*batch_size
        ignore_crit = nn.CrossEntropyLoss(reduction='none').cuda()       
        # forward
        logits = self.v_net(trn_X)
        loss = torch.dot(torch.sigmoid(Likelihood[step*batch_size:dataIndex]), ignore_crit(logits, trn_y))/(torch.sigmoid(Likelihood[step*batch_size:dataIndex]).sum())
        logger.info("unrolled weighted loss = {}".format(loss)) 
        
        # compute gradient
        gradients = torch.autograd.grad(loss, self.v_net.weights(), create_graph=True)
        
        vw_stars = []
        # do virtual step (update gradient)
        
            # dict key is not the value, but the pointer. So original network weight have to
            # be iterated also.
        for w, vw, g in zip(self.net.weights(), self.v_net.weights(), gradients):
            m = w_optim.state[w].get('momentum_buffer', 0.) * self.w_momentum              
            vw_star = w - xi * (m + g + self.w_weight_decay*w)   
#             loss_ll = torch.autograd.grad(loss, Likelihood, retain_graph=True)
#             print('loss-to-likelihood gradient is:', loss_ll[0].size())
#             w_ll = torch.autograd.grad(vw_star, Likelihood, grad_outputs=torch.ones_like(vw_star), retain_graph=True)
#             grads.append(w_ll)
#             print('weight-to-likelihood gradient is:', w_ll)
            
            vw_stars.append(vw_star)
            with torch.no_grad():
                vw.copy_(w - xi * (m + g + self.w_weight_decay*w)) 
        
        vw_stars.backward()
        # below operations do not need gradient tracking
        with torch.no_grad():
            # synchronize alphas
            for a, va in zip(self.net.alphas(), self.v_net.alphas()):
                va.copy_(a)
        print(Likelihood.grad) 
        '''       
        # do virtual step (update gradient)
        
        # dict key is not the value, but the pointer. So original network weight have to
        # be iterated also.
        for i, (w, vw, g) in enumerate(zip(self.net.weights(), self.v_net.weights(), gradients)):
            m = w_optim.state[w].get('momentum_buffer', 0.) * self.w_momentum
        # in-place operation not used
            vw = w - xi * (m + g + self.w_weight_decay*w)    
            print('vw,', vw.size)
            print('vitual weight gradient is:', torch.autograd.grad(vw, Likelihood).size())
            print('self_weight1,', self.v_net.weights()[i])
               
            self.v_net.weights()[i] = w - xi * (m + g + self.w_weight_decay*w)
#             self.v_net.weights()[i].data = (w - xi * (m + g + self.w_weight_decay*w)).clone()
#             self.v_net.weights()[i].grad = (w - xi * (m + g + self.w_weight_decay*w)).grad

            print('self_weight2,', self.v_net.weights()[i])
            print('weight gradient is:', torch.autograd.grad(torch.sum(self.v_net.weights()[i]), Likelihood, retain_graph=True))
        for w, vw, g in zip(self.net.weights(), self.v_net.weights(), gradients):
            print('weight gradient is:', torch.autograd.grad(torch.sum(vw), Likelihood, retain_graph=True))
            
        
        with torch.no_grad():
            # synchronize alphas
            for a, va in zip(self.net.alphas(), self.v_net.alphas()):
                va.copy_(a)
        '''
        return loss, vw_stars
        
    def unrolled_backward(self, trn_X, trn_y, val_X, val_y, xi, w_optim, model, Likelihood, batch_size, step):
        """ Compute unrolled loss and backward its gradients
        Args:
            xi: learning rate for virtual gradient step (same as net lr)
            w_optim: weights optimizer - for virtual step
        """
        # do virtual step (calc w`)
        tloss, grads = self.virtual_step(trn_X, trn_y, xi, w_optim, model, Likelihood, batch_size, step)
        print('size', len(grads))
        # calc unrolled loss
        loss = self.v_net.loss(val_X, val_y) # L_val(w`)

        # compute gradient
        v_alphas = tuple(self.v_net.alphas())
        v_weights = tuple(self.v_net.weights())
#         v_grads = torch.autograd.grad(loss, v_alphas + v_weights + tuple(Likelihood), allow_unused=True)
    
        v_grads = torch.autograd.grad(loss, v_alphas + v_weights, retain_graph=True)
        dalpha = v_grads[:len(v_alphas)]
        dw = v_grads[len(v_alphas):]
        
        
        # gradient of validation loss to weights in the network  (1399*(48*3*3*)
        dvloss_w = torch.autograd.grad(loss, self.v_net.weights())
        print(self.v_net.weights()[0].size())   
        print('dvloss_w size', len(dvloss_w))
        print('dvloss_w size', dvloss_w[0].size())
        dlikelihood = torch.autograd.grad(loss, tloss)
#         dalpha = v_grads[:len(v_alphas)]
#         dw = v_grads[len(v_alphas):(len(v_alphas)+len(v_weights))]

        hessian = self.compute_hessian(dw, trn_X, trn_y)

        # update final gradient = dalpha - xi*hessian
        with torch.no_grad():
            for alpha, da, h in zip(self.net.alphas(), dalpha, hessian):
                alpha.grad = da - xi*h
#         for likelihood, dl in zip(Likelihood, v_grads[(len(v_alphas)+len(v_weights)):]):
#             likelihood.grad = dl
        for likelihood, dl in zip(Likelihood, dlikelihood):
            likelihood.grad = dl

    def compute_hessian(self, dw, trn_X, trn_y):
        """
        dw = dw` { L_val(w`, alpha) }
        w+ = w + eps * dw
        w- = w - eps * dw
        hessian = (dalpha { L_trn(w+, alpha) } - dalpha { L_trn(w-, alpha) }) / (2*eps)
        eps = 0.01 / ||dw||
        """
        norm = torch.cat([w.view(-1) for w in dw]).norm()
        eps = 0.01 / norm

        # w+ = w + eps*dw`
        with torch.no_grad():
            for p, d in zip(self.net.weights(), dw):
                p += eps * d
        loss = self.net.loss(trn_X, trn_y)
        dalpha_pos = torch.autograd.grad(loss, self.net.alphas()) # dalpha { L_trn(w+) }

        # w- = w - eps*dw`
        with torch.no_grad():
            for p, d in zip(self.net.weights(), dw):
                p -= 2. * eps * d
        loss = self.net.loss(trn_X, trn_y)
        dalpha_neg = torch.autograd.grad(loss, self.net.alphas()) # dalpha { L_trn(w-) }

        # recover w
        with torch.no_grad():
            for p, d in zip(self.net.weights(), dw):
                p += eps * d

#         hessian = [(p-n) / 2.*eps for p, n in zip(dalpha_pos, dalpha_neg)]
        hessian = [(p-n) / (2.*eps) for p, n in zip(dalpha_pos, dalpha_neg)]
        return hessian
